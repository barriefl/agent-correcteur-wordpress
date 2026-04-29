import json
import time

from google import genai
from google.genai import types


def evaluer_site_via_ia(api_key, audit_json, grille_texte, max_retries=5):
    """
    Envoie l'audit et la grille à Gemini et force une réponse en JSON.
    Inclut un système de Retry en cas de surcharge de l'API.
    """
    client = genai.Client(api_key=api_key)
    model_id = "gemini-2.5-flash"

    prompt = f"""
    Tu es un professeur expert en développement web, SEO et marketing digital.
    Ton objectif est d'évaluer le travail d'un étudiant à partir d'un audit technique de son site WordPress.

    Voici la grille de notation officielle (issue d'un fichier Excel) :
    ---
    {grille_texte}
    ---

    Voici l'audit technique complet du site de l'étudiant au format JSON :
    ---
    {json.dumps(audit_json)}
    ---

    INSTRUCTIONS STRICTES :
    1. Analyse les données de l'audit et croise-les avec les critères de la grille de notation.
    2. Attribue les points de la manière la plus juste et stricte possible.
    3. Rédige un bref commentaire global constructif.
    4. Retourne ton résultat UNIQUEMENT selon la structure JSON suivante (n'invente pas de nouvelles clés) :
    {{
        "notes_detaillees": {{
            "Nom du critere 1": note_en_chiffre,
            "Nom du critere 2": note_en_chiffre
        }},
        "note_totale": note_finale_en_chiffre,
        "commentaires_globaux": "Ton commentaire explicatif ici..."
    }}
    """

    for tentative in range(max_retries):
        try:
            reponse = client.models.generate_content(
                model=model_id,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json"
                ),
            )

            texte_brut = reponse.text

            # --- VÉRIFICATION DE LA SYNTAXE. ---
            try:
                resultat_json = json.loads(texte_brut)
            except json.JSONDecodeError:
                raise ValueError("Réponse de l'IA non conforme au format JSON attendu.")

            # --- VÉRIFICATION DE LA STRUCTURE JSON. ---
            cles_obligatoires = [
                "notes_detaillees",
                "note_totale",
                "commentaires_globaux",
            ]
            cles_manquantes = [
                cle for cle in cles_obligatoires if cle not in resultat_json
            ]

            if cles_manquantes:
                raise ValueError(
                    f"JSON valide mais incomplet. Clés manquantes : {', '.join(cles_manquantes)}"
                )

            return resultat_json

        except Exception as e:
            msg = str(e).lower()

            if "quota" in msg or "exhausted" in msg:
                raise Exception(
                    "Limite de quota journalier atteinte. Arrêt du processus."
                )

            if tentative < max_retries - 1:
                temps_attente = 15 * (2**tentative)

                if "429" in msg or "too many requests" in msg:
                    temps_attente = max(temps_attente, 60)

                print(
                    f"Surcharge API ou JSON invalide (Tentative {tentative + 1}/{max_retries}). Attente de {temps_attente}s avant de réessayer..."
                )
                time.sleep(temps_attente)
            else:
                raise Exception(f"Échec de l'IA après {max_retries} tentatives : {e}.")
