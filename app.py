import streamlit as st
import PyPDF2
from google.cloud import secretmanager
from atlassian import Confluence
from google import genai

# ==========================================
# 1. FONCTIONS DE CONFIGURATION & CONNEXION
# ==========================================

@st.cache_resource
def get_confluence_client():
    """Récupère le token via Secret Manager et initialise le client Confluence."""
    try:
        sm_client = secretmanager.SecretManagerServiceClient()
        secret_name = "projects/ofr-dxe-data-storage-in-prd/secrets/Token_export_to_confluence/versions/latest"
        secret_response = sm_client.access_secret_version(request={"name": secret_name})
        confluence_token = secret_response.payload.data.decode("UTF-8").strip()

        confluence = Confluence(
            url='https://espace.agir.orange.com',
            token=confluence_token,
            timeout=120
        )
        return confluence
    except Exception as e:
        st.error(f"Erreur lors de la connexion à Confluence / Secret Manager: {e}")
        return None


@st.cache_data(ttl=300)
def get_space_info(_confluence, space_key="DFI2E"):
    """Récupère directement les infos d'un espace spécifique pour éviter les timeouts."""
    try:
        space = _confluence.get_space(space_key, expand='homepage')
        homepage_id = space.get('homepage', {}).get('id') if space.get('homepage') else None
        return {"key": space['key'], "name": space['name'], "homepage_id": homepage_id}
    except Exception as e:
        st.error(f"Erreur lors de la récupération de l'espace {space_key}: {e}")
        return None


@st.cache_data(ttl=120)
def get_child_pages(_confluence, page_id):
    """Récupère les pages enfants d'une page donnée."""
    if not page_id:
        return {}
    try:
        children = _confluence.get_page_child_by_type(page_id, type='page', start=0, limit=100)
        return {c['title']: c['id'] for c in children}
    except Exception:
        return {}


# ==========================================
# 2. FONCTIONS DE TRAITEMENT
# ==========================================

def extract_text_from_pdf(uploaded_file):
    reader = PyPDF2.PdfReader(uploaded_file)
    text = ""
    for page in reader.pages:
        text += page.extract_text() + "\n"
    return text


def get_genai_client():
    return genai.Client(
        vertexai=True,
        project="ofr-dxe-data-uc-ia-lab-prd",
        location="europe-west9"
    )


def clean_xhtml(html_result):
    html_result = html_result.replace("<br>", "<br/>").replace("<hr>", "<hr/>")
    html_result = html_result.replace("```html", "").replace("```", "").strip()
    return html_result


def analyze_with_ai(text, palette):
    """Génère une première version du résumé HTML à partir du texte du PDF."""
    client = get_genai_client()

    prompt = f"""
    Tu es un assistant professionnel. Voici un texte extrait d'un PDF :

    {text}

    Instructions :
    1. Fais un résumé structuré et clair de ce document.
    2. Formate le résultat en HTML (uniquement le code interne, sans les balises <html>, <head> ou <body>).
    3. Utilise la palette de couleurs suivante pour les styles CSS inline : {palette}.
    4. IMPORTANT : Le code généré DOIT être du XHTML strict valide (pour Confluence).
       - Toutes les balises vides DOIVENT être auto-fermantes (ex: utilise <br/> et NON <br>, <hr/> et NON <hr>, <img ... /> et NON <img ...>).
       - Toutes les balises ouvertes doivent être correctement refermées.
       - N'utilise pas de caractères spéciaux non échappés (utilise &amp; pour &, &lt; pour <, &gt; pour >).
       - Tous les attributs doivent être entre guillemets.
    5. Réponds UNIQUEMENT avec le code HTML/XHTML.
    6. Inclure toutes les fonctions mathématiques s'ils existent.
    7. Evite ```html au début et ``` à la fin.
    8. Lister toutes les règles de gestion.
    9. Inclure toujours les contacts, format Confluence @Prénom, nom s'ils existent.
    """

    response = client.models.generate_content(
        model="gemini-2.5-pro",
        contents=prompt
    )

    return clean_xhtml(response.text)


def regenerate_with_feedback(current_html, feedback, palette):
    """Régénère le HTML existant en tenant compte des instructions de modification données par l'utilisateur."""
    client = get_genai_client()

    prompt = f"""
    Tu es un assistant professionnel. Voici un contenu HTML/XHTML déjà généré pour une page Confluence :

    {current_html}

    L'utilisateur souhaite apporter les modifications suivantes :
    "{feedback}"

    Instructions :
    1. Applique les modifications demandées par l'utilisateur au contenu existant.
    2. Conserve la structure générale et le style si l'utilisateur ne demande pas explicitement de le changer.
    3. Utilise la palette de couleurs suivante pour les styles CSS inline : {palette}.
    4. IMPORTANT : Le code généré DOIT être du XHTML strict valide (pour Confluence).
       - Toutes les balises vides DOIVENT être auto-fermantes (ex: <br/>, <hr/>, <img ... />).
       - Toutes les balises ouvertes doivent être correctement refermées.
       - N'utilise pas de caractères spéciaux non échappés (utilise &amp;, &lt;, &gt;).
       - Tous les attributs doivent être entre guillemets.
    5. Réponds UNIQUEMENT avec le code HTML/XHTML final, sans commentaire ni explication.
    6. Evite ```html au début et ``` à la fin.
    7. Inclure toutes les fonctions mathématiques s'ils existent en format Latex.
    8. Lister toutes les règles de gestion.
    9. Inclure toujours les contacts, format Confluence @Prénom, nom.
    """

    response = client.models.generate_content(
        model="gemini-2.5-pro",
        contents=prompt
    )

    return clean_xhtml(response.text)


def publish_to_confluence(confluence, page_id, html_content, custom_title=None):
    try:
        page_id = str(page_id).strip()
        page = confluence.get_page_by_id(page_id)
        current_title = page.get("title", "")
        final_title = custom_title.strip() if custom_title and custom_title.strip() else current_title

        confluence.update_page(
            page_id=page_id,
            title=final_title,
            body=html_content,
            parent_id=None,
            type='page',
            representation='storage',
            minor_edit=False,
            always_update=True
        )
        return True
    except Exception as e:
        st.error(f"Erreur détaillée lors de la mise à jour : {e}")
        return False


def create_in_confluence(confluence, space_key, title, html_content, parent_id=None):
    try:
        confluence.create_page(
            space=space_key,
            title=title,
            body=html_content,
            parent_id=parent_id,
            type='page',
            representation='storage',
            editor='v2'
        )
        return True
    except Exception as e:
        st.error(f"Erreur détaillée lors de la création : {e}")
        return False


# ==========================================
# 3. INTERFACE STREAMLIT
# ==========================================

st.set_page_config(page_title="Générateur Confluence via IA", layout="wide")

st.markdown(
    """
    <div style="display: flex; align-items: center; flex-direction: column; background-color: #f5f7fa; padding: 20px; border-radius: 12px; box-shadow: 0 4px 12px rgba(0,0,0,0.08); border: 1px solid #e0e6ed; text-align: center;">
        <div style="display: flex; align-items: center; justify-content: center; margin-bottom: 10px;">
            <img src="https://cdn.iconscout.com/icon/free/png-512/free-confluence-logo-icon-svg-download-png-3029929.png?f=webp&w=256" width="45" style="margin-right: 12px;" />
            <h1 style="font-size: 28px; margin: 0; color: #172b4d; font-weight: 600;">
                Smart Confluence AI
            </h1>
        </div>
        <p style="margin: 0; font-size: 15px; color: #4a5568;">
            Chargez un PDF, laissez l'IA l'analyser et le formater, puis publiez-le directement sur Confluence.
        </p>
    </div>
    """,
    unsafe_allow_html=True
)

confluence_client = get_confluence_client()

st.subheader("1. Configuration")
uploaded_pdf = st.file_uploader("Chargez votre fichier PDF", type="pdf")

palettes_data = {
    "Corporate Orange": {"colors": ["#F16E00", "#333333", "#E8E8E8"], "desc": "Titres en Orange, texte en gris foncé, fond gris clair"},
    "Tech Blue": {"colors": ["#003366", "#0099FF", "#F0F8FF"], "desc": "Titres en Bleu marine, accents en bleu clair, fond azur"},
    "Dark Mode": {"colors": ["#00FF00", "#1E1E1E", "#000000"], "desc": "Titres en Vert fluo, éléments gris foncé, fond noir"},
    "Nature Green": {"colors": ["#2E8B57", "#556B2F", "#F5FFFA"], "desc": "Titres vert lagon, texte olive, fond menthe"},
    "Sunset Warm": {"colors": ["#D2691E", "#FF4500", "#FFF5EE"], "desc": "Titres chocolat, accents orange vif, fond coquillage"},
    "Elegant Purple": {"colors": ["#4B0082", "#8A2BE2", "#F8F8FF"], "desc": "Titres indigo, accents violet, fond fantôme"}
}

if "palette_choice" not in st.session_state:
    st.session_state.palette_choice = "Corporate Orange"

st.write("### 🎨 Choisissez une palette de couleurs")

palette_items = list(palettes_data.items())
for i in range(0, len(palette_items), 3):
    cols = st.columns(3)
    for j in range(3):
        if i + j < len(palette_items):
            name, data = palette_items[i + j]
            with cols[j]:
                is_selected = st.session_state.palette_choice == name
                border_color = "#FF4B4B" if is_selected else "#E0E0E0"
                st.markdown(f"""
                    <div style="display: flex; height: 60px; border-radius: 10px; overflow: hidden; border: 3px solid {border_color}; box-shadow: 0 4px 6px rgba(0,0,0,0.1); margin-bottom: 10px; transition: 0.3s;">
                        <div style="flex: 1; background-color: {data['colors'][0]};"></div>
                        <div style="flex: 1; background-color: {data['colors'][1]};"></div>
                        <div style="flex: 1; background-color: {data['colors'][2]};"></div>
                    </div>
                """, unsafe_allow_html=True)
                if st.button(name, key=f"btn_{name}", use_container_width=True):
                    st.session_state.palette_choice = name
                    st.rerun()

st.divider()

selected = st.session_state.palette_choice
st.success(f"**Palette active :** {selected}")

st.subheader("2. Destination Confluence")

action_type = st.radio("Que souhaitez-vous faire ?", ["Créer une nouvelle page", "Mettre à jour une page existante"], horizontal=True)

final_page_id = None
final_space_key = None
final_parent_id = None
final_new_title = None

if action_type == "Mettre à jour une page existante":
    col_id, col_title = st.columns(2)
    with col_id:
        final_page_id = st.text_input("ID de la page Confluence", help="L'identifiant numérique de la page (ex: 12345678)")
    with col_title:
        final_new_title = st.text_input("Nouveau titre de la page (optionnel)", help="Laissez vide pour conserver le titre actuel")

else:
    if confluence_client:
        TARGET_SPACE_KEY = "DFI2E"
        with st.spinner(f"Chargement de l'espace {TARGET_SPACE_KEY}..."):
            space_info = get_space_info(confluence_client, TARGET_SPACE_KEY)

        if space_info:
            st.info(f"📍 Publication configurée pour l'espace **{space_info['name']}** ({space_info['key']})")
            final_space_key = space_info["key"]
            homepage_id = space_info["homepage_id"]

            # Initialisation de la navigation dans la session
            if "nav_path" not in st.session_state:
                st.session_state.nav_path = [homepage_id]  # racine par défaut

            current_parent_id = homepage_id
            niveau = 1

            while True:
                children_dict = get_child_pages(confluence_client, current_parent_id)

                if not children_dict:
                    # Plus aucun enfant : on s'arrête ici
                    break

                options = ["(Placer ici - ne pas descendre plus loin)"] + list(children_dict.keys())
                selected_name = st.selectbox(
                    f"{niveau + 1}. Sélectionnez le niveau {niveau}",
                    options,
                    key=f"level_{niveau}"
                )

                if selected_name == "(Placer ici - ne pas descendre plus loin)":
                    break

                current_parent_id = children_dict[selected_name]
                niveau += 1

            final_parent_id = current_parent_id
            final_new_title = st.text_input(
                f"{niveau + 1}. Titre de la nouvelle page",
                value="Nouvelle page générée par IA"
            )
        else:
            # Mode manuel inchangé
            st.warning("⚠️ L'API Confluence ne parvient pas à récupérer l'espace DFI2E. Mode de saisie manuelle activé.")
            final_space_key = st.text_input("1. Clé de l'Espace", value="DFI2E")
            final_parent_id = st.text_input("2. ID de la page parente (Optionnel)")
            final_new_title = st.text_input("3. Titre de la nouvelle page", value="Nouvelle page générée par IA")
    else:
        st.warning("Client Confluence non initialisé.")

st.subheader("3. Traitement")

if "html_result" not in st.session_state:
    st.session_state.html_result = None
if "pdf_text" not in st.session_state:
    st.session_state.pdf_text = None

if st.button("Lancer l'analyse et prévisualiser", type="primary"):
    if not uploaded_pdf:
        st.warning("Veuillez charger un fichier PDF.")
    elif action_type == "Mettre à jour une page existante" and not final_page_id:
        st.warning("Veuillez renseigner l'ID de la page Confluence à mettre à jour.")
    elif action_type == "Créer une nouvelle page" and not final_new_title:
        st.warning("Veuillez donner un titre à la nouvelle page.")
    elif not confluence_client:
        st.error("Impossible de se connecter à Confluence. Vérifiez les credentials Secret Manager.")
    else:
        with st.spinner("Extraction du texte du PDF..."):
            st.session_state.pdf_text = extract_text_from_pdf(uploaded_pdf)

        with st.spinner("Analyse par l'Agent IA en cours..."):
            selected_palette_desc = palettes_data[selected]
            st.session_state.html_result = analyze_with_ai(st.session_state.pdf_text, selected_palette_desc)

# ==========================================
# 4. Prévisualisation
# ==========================================

if st.session_state.html_result:
    html_result = st.session_state.html_result

    st.subheader("4. Prévisualisation")

    st.markdown("### Prévisualisation avant publication")
    st.components.v1.html(html_result, height=700, scrolling=True)

    with st.expander("Aperçu du code HTML/XHTML généré", expanded=False):
        st.code(html_result, language="html")

    st.markdown("### ✏️ Modifier")
    st.caption("Vous pouvez demander à l'IA d'ajuster le contenu ci-dessus (ex : « raccourcis le résumé », « ajoute une section conclusion », « change le ton »...).")

    feedback_instructions = st.text_area(
        "Indiquez vos instructions de modification",
        placeholder="Ex : Réduis la longueur du résumé et ajoute une liste à puces pour les points clés.",
        key="feedback_instructions"
    )

    if st.button("🔄 Régénérer la prévisualisation avec ces instructions"):
        if not feedback_instructions or not feedback_instructions.strip():
            st.warning("Veuillez saisir des instructions avant de régénérer.")
        else:
            with st.spinner("Régénération par l'Agent IA en cours..."):
                selected_palette_desc = palettes_data[selected]
                st.session_state.html_result = regenerate_with_feedback(
                    st.session_state.html_result,
                    feedback_instructions,
                    selected_palette_desc
                )
            st.rerun()

# ==========================================
# 5. Publication
# ==========================================

if st.session_state.html_result:
    st.subheader("5. Publication")

    if st.button("🚀 Publier sur Confluence", type="primary", use_container_width=True):
        with st.spinner("Publication sur Confluence..."):
            if action_type == "Mettre à jour une page existante":
                success = publish_to_confluence(confluence_client, final_page_id, st.session_state.html_result, custom_title=final_new_title)
                if success:
                    st.success(f"✔ Page {final_page_id} mise à jour avec succès sur Confluence !")
                    st.toast("Mise à jour réussie !", icon="🎉")
            else:
                # S'assurer que le parent_id est bien formaté
                clean_parent_id = str(final_parent_id).strip() if final_parent_id else None

                success = create_in_confluence(confluence_client, final_space_key.strip(), final_new_title, st.session_state.html_result, parent_id=clean_parent_id)
                if success:
                    st.success(f"✔ Nouvelle page '{final_new_title}' créée avec succès dans l'espace {final_space_key.strip()} !")
                    st.toast("Création réussie !", icon="🎉")
st.set_page_config(layout="wide")
st.title("✅ Test de déploiement réussi !")
st.write("Si vous voyez ce message, cela signifie que :")
st.info("1. Le `Dockerfile` est correct.\n2. Cloud Build a fonctionné.\n3. Cloud Run a bien démarré le conteneur.")
st.balloons()
