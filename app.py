import glob
import io
import json
import os
import time
import zipfile
from urllib.parse import urlparse

import pandas as pd
import streamlit as st

from ai_grader import evaluer_site_via_ia
from scraping import crawler_et_scraper

# --- CONFIGURATION. ---
st.set_page_config(
    page_title="Agent Correcteur WordPress", page_icon="🕵️‍♂️", layout="wide"
)

# --- GESTION DE LA MÉMOIRE (SESSION STATE). ---
if "audits_scrapes" not in st.session_state:
    st.session_state.audits_scrapes = {}
if "resultats_ia" not in st.session_state:
    st.session_state.resultats_ia = []

# --- BARRE LATÉRALE. ---
with st.sidebar:
    st.header("Configuration IA")
    api_key = st.text_input(
        "Clé API Gemini :",
        type="password",
        help="Obtenez votre clé sur Google AI Studio (https://aistudio.google.com).",
    )

    st.divider()

    st.header("Grille de Notation")
    fichier_grille = st.file_uploader(
        "Importer la grille (Fichier Excel .xlsx)", type=["xlsx"]
    )

    grille_texte = ""
    if fichier_grille:
        df_grille = pd.read_excel(fichier_grille)
        grille_texte = df_grille.to_csv(index=False)
        st.success("Grille chargée.")

    st.divider()

    st.header("Sécurité & Vitesse")
    utiliser_cache = st.checkbox(
        "Utiliser le cache (Ne pas re-scraper les JSON existants)", value=True
    )
    utiliser_checkpoint = st.checkbox(
        "Reprendre l'évaluation IA là où elle s'est arrêtée", value=True
    )

    st.divider()

    st.header("Sauvegarde (Workspace)")

    # RESTAURATION DU WORKSPACE.
    fichier_zip = st.file_uploader("Restaurer un Workspace (.zip)", type=["zip"])
    if fichier_zip:
        if st.button("Restaurer les fichiers", width="stretch"):
            os.makedirs("data", exist_ok=True)
            with zipfile.ZipFile(fichier_zip, "r") as zip_ref:
                zip_ref.extractall("data")
            st.success(
                "Workspace restauré ! (Cliquez sur 'Lancer l'analyse' pour charger la mémoire)."
            )

    # TÉLÉCHARGEMENT DU WORKSPACE.
    if os.path.exists("data") and os.listdir("data"):
        buffer_zip = io.BytesIO()
        with zipfile.ZipFile(buffer_zip, "w", zipfile.ZIP_DEFLATED) as zipf:
            for root, _, files in os.walk("data"):
                for file in files:
                    chemin_complet = os.path.join(root, file)
                    zipf.write(chemin_complet, arcname=file)

        st.download_button(
            label="Télécharger le Workspace (.zip)",
            data=buffer_zip.getvalue(),
            file_name=f"workspace_audit_{int(time.time())}.zip",
            mime="application/zip",
            width="stretch",
        )
    else:
        st.info("Le workspace est vide pour le moment.")

# --- ZONE PRINCIPALE. ---
st.title("Agent Correcteur WordPress")
st.markdown(
    "Scrapez les sites de vos étudiants puis laissez l'IA les noter automatiquement selon votre barème."
)

# --- CHAMP DE SAISIE. ---
urls_input = st.text_area(
    "Entrez les URLs à analyser (une par ligne) :",
    value="http://51.83.36.122/eg/demo-tc/",
    height=150,
)
liste_urls = [url.strip() for url in urls_input.split("\n") if url.strip()]

col1, col2 = st.columns(2)

# --- SCRAPING (+ CACHE). ---
with col1:
    if st.button("Lancer l'analyse", type="primary", width="stretch"):
        if not liste_urls:
            st.error("Veuillez entrer au moins une URL.")
        else:
            st.session_state.audits_scrapes = {}

            for url in liste_urls:
                st.divider()
                st.subheader(f"Traitement de : {url}.")
                status_box = st.empty()

                def interface_logger(message):
                    status_box.info(message)

                with st.spinner("Analyse du site en cours..."):
                    try:
                        # GESTION DU CACHE
                        parsed = urlparse(url)
                        chemin = parsed.path.strip("/").replace("/", "_")
                        nom_base = (
                            f"{parsed.netloc}_{chemin}" if chemin else parsed.netloc
                        )
                        nom_base = nom_base.replace(":", "_")

                        fichiers_cache = glob.glob(f"data/AUDIT_{nom_base}_*.json")

                        if utiliser_cache and fichiers_cache:
                            fichier_recent = sorted(fichiers_cache)[-1]
                            chemin_fichier = fichier_recent
                            with open(fichier_recent, "r", encoding="utf-8") as f:
                                donnees_extraites = json.load(f)
                            status_box.success(
                                f"Chargé depuis le cache local : {fichier_recent.split('/')[-1]}"
                            )
                            st.session_state.audits_scrapes[url] = donnees_extraites
                        else:
                            donnees_extraites, chemin_fichier = crawler_et_scraper(
                                url, log_callback=interface_logger
                            )

                            st.session_state.audits_scrapes[url] = donnees_extraites

                            status_box.success(
                                f"Scraping terminé ! ({len(donnees_extraites['pages'])} pages analysées)."
                            )

                        with st.expander(
                            f"Voir les données générées (Sauvegardé dans {chemin_fichier})."
                        ):
                            st.json(donnees_extraites)

                            json_string = json.dumps(
                                donnees_extraites, indent=4, ensure_ascii=False
                            )

                            st.download_button(
                                label="Télécharger le fichier JSON",
                                file_name=chemin_fichier.split("/")[-1],
                                mime="application/json",
                                data=json_string,
                            )
                    except Exception as e:
                        status_box.error(f"Une erreur critique est survenue : {e}.")
# --- CORRECTION IA (+ CHECKPOINT). ---
with col2:
    bouton_ia_desactive = not (
        st.session_state.audits_scrapes and api_key and fichier_grille
    )

    if st.button(
        "Lancer la correction par l'IA",
        type="primary",
        disabled=bouton_ia_desactive,
        width="stretch",
    ):
        barre_progression = st.progress(0)
        status_ia = st.empty()
        fichier_checkpoint = "data/checkpoint_notes_ia.csv"

        # GESTION DU CHECKPOINT.
        urls_deja_faites = []
        if utiliser_checkpoint and os.path.exists(fichier_checkpoint):
            df_checkpoint = pd.read_csv(fichier_checkpoint)
            st.session_state.resultats_ia = df_checkpoint.to_dict("records")
            urls_deja_faites = df_checkpoint["URL Étudiant"].tolist()
            status_ia.info(
                f"Reprise activée : {len(urls_deja_faites)} sites déjà notés."
            )
        else:
            st.session_state.resultats_ia = []
            if os.path.exists(fichier_checkpoint):
                os.remove(fichier_checkpoint)

        total_sites = len(st.session_state.audits_scrapes)

        for index, (url, audit) in enumerate(st.session_state.audits_scrapes.items()):
            if url in urls_deja_faites:
                status_ia.success(f"> {url} déjà évalué.")
                barre_progression.progress((index + 1) / total_sites)
                continue

            status_ia.info(f"Correction en cours pour : {url} ...")

            try:
                resultat_json = evaluer_site_via_ia(api_key, audit, grille_texte)

                ligne_excel = {
                    "URL Étudiant": url,
                    "Note Totale": resultat_json.get("note_totale", 0),
                    "Commentaires": resultat_json.get("commentaires_globaux", ""),
                }
                ligne_excel.update(resultat_json.get("notes_detaillees", {}))

                st.session_state.resultats_ia.append(ligne_excel)

                pd.DataFrame(st.session_state.resultats_ia).to_csv(
                    fichier_checkpoint, index=False
                )

                status_ia.success(
                    f"Correction terminée pour : {url} (Note : {resultat_json.get('note_totale')})."
                )

                if index < total_sites - 1:
                    status_ia.warning(
                        "Pause de 4 secondes pour respecter les limites de l'API Google..."
                    )
                    time.sleep(4)

            except Exception as e:
                status_ia.error(f"Erreur de l'IA pour {url} : {e}.")
                break

            barre_progression.progress((index + 1) / total_sites)

# --- RÉSULTATS ET TÉLÉCHARGEMENT. ---
if st.session_state.resultats_ia:
    st.divider()
    st.header("Résultats des Évaluations")

    df_resultats = pd.DataFrame(st.session_state.resultats_ia)
    st.dataframe(df_resultats, width="stretch")

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df_resultats.to_excel(writer, index=False, sheet_name="Notes Etudiants")

    st.download_button(
        label="Télécharger le bilan (Excel)",
        data=buffer.getvalue(),
        file_name="Bilan_Notes_WordPress.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary",
    )
