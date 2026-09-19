import streamlit as st

st.set_page_config(
    page_title="Test Cloud Run",
    layout="wide"
)

st.title("🚀 Cloud Run + Streamlit")
st.success("Le conteneur fonctionne correctement !")
st.write("Streamlit répond correctement sur le port Cloud Run.")
