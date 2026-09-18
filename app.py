import streamlit as st

st.set_page_config(layout="wide")
st.title("✅ Test de déploiement réussi !")
st.write("Si vous voyez ce message, cela signifie que :")
st.info("1. Le `Dockerfile` est correct.\n2. Cloud Build a fonctionné.\n3. Cloud Run a bien démarré le conteneur.")
st.balloons()
