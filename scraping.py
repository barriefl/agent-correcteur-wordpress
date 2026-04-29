import json
import os
import time
from datetime import datetime
from urllib.parse import urljoin, urlparse

import cloudscraper
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def obtenir_poids_image(url_image, session):
    """Demande la taille d'une image au serveur sans la télécharger."""
    if url_image.startswith("data:"):
        return 0
    try:
        reponse = session.get(url_image, stream=True, timeout=10)
        if "Content-Length" in reponse.headers:
            return int(reponse.headers["Content-Length"])
        return 0
    except Exception:
        return -1


def rendre_chemin_relatif(url, domaine):
    """Transforme une URL absolue en chemin relatif pour alléger le JSON"""
    parsed = urlparse(url)
    if parsed.netloc == domaine:
        return parsed.path if parsed.path else "/"
    return url


def crawler_et_scraper(url_depart, log_callback=None):
    parsed_url = urlparse(url_depart)
    chemin = parsed_url.path.strip("/").replace("/", "_")
    nom_base = f"{parsed_url.netloc}_{chemin}" if chemin else parsed_url.netloc
    nom_base = nom_base.replace(":", "_")

    domaine_cible = parsed_url.netloc
    urls_a_visiter = [url_depart]
    urls_visitees = set()
    donnees_du_site = []
    plugins_globaux = set()

    site_a_logo = False
    site_a_favicon = False

    def log(message):
        """Petite fonction interne pour gérer l'affichage terminal vs interface"""
        if log_callback:
            log_callback(message)
        else:
            print(message)

    # --- CLOUDSCRAPER. ---
    session = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "desktop": True}
    )

    # --- RETRIES. ---
    retries = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
    session.mount("http://", HTTPAdapter(max_retries=retries))
    session.mount("https://", HTTPAdapter(max_retries=retries))

    log(f"Lancement de l'araignée sur : {url_depart}.")

    while urls_a_visiter:
        url_courante = urls_a_visiter.pop(0).split("#")[0]

        if url_courante in urls_visitees:
            continue

        log(f"[Page n°{len(urls_visitees) + 1}] Analyse de : {url_courante}.")
        urls_visitees.add(url_courante)

        try:
            # --- ANALYSE DE LA PAGE. ---
            reponse = session.get(url_courante, timeout=10)

            if "text/html" not in reponse.headers.get("Content-Type", ""):
                continue

            soup = BeautifulSoup(reponse.text, "html.parser")

            # --- IDENTITÉ. ---
            if not site_a_logo and soup.find("img", class_="custom-logo"):
                site_a_logo = True
            if not site_a_favicon and soup.find(
                "link", rel=lambda r: r and "icon" in r.lower()
            ):
                site_a_favicon = True

            # --- ARCHITECTURE. ---
            body_classes = soup.body.get("class", []) if soup.body else []
            est_un_article = "single-post" in body_classes
            nombre_menus = len(soup.find_all("nav"))
            a_contenu_mobile = bool(soup.find(class_=lambda c: c and "scfm-" in c))

            # --- SEO. ---
            meta_desc_tag = soup.find("meta", attrs={"name": "description"})
            meta_description = (
                meta_desc_tag["content"].strip()
                if meta_desc_tag and meta_desc_tag.get("content")
                else ""
            )
            titre_onglet = soup.title.string.strip() if soup.title else ""

            # --- HIÉRARCHIE DES TITRES. ---
            hierarchie_titres = [
                f"{h.name.upper()} - {h.text.strip()}"
                for h in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"])
            ]

            # --- PLUGINS WORDPRESS (FRONT-END UNIQUEMENT). ---
            plugins_trouves = set()
            for balise in soup.find_all(["link", "script"]):
                lien = balise.get("href") or balise.get("src")

                if lien and "/wp-content/plugins/" in lien:
                    parties = lien.split("/wp-content/plugins/")

                    if len(parties) > 1:
                        reste_du_lien = parties[1]
                        nom_plugin = reste_du_lien.split("/")[0]

                        if nom_plugin:
                            plugins_trouves.add(nom_plugin)
                            plugins_globaux.add(nom_plugin)

            # --- LIENS ET RÉSEAU. ---
            liens_externes_testes = {}
            liens_internes_trouves = 0

            for balise_a in soup.find_all("a", href=True):
                lien_trouve = urljoin(url_courante, balise_a["href"])
                parsed_lien = urlparse(lien_trouve)

                if parsed_lien.netloc == domaine_cible:
                    liens_internes_trouves += 1
                    lien_propre = lien_trouve.split("#")[0]
                    if (
                        lien_propre not in urls_visitees
                        and lien_propre not in urls_a_visiter
                    ):
                        urls_a_visiter.append(lien_propre)
                elif parsed_lien.scheme in ["http", "https"]:
                    if lien_trouve not in liens_externes_testes:
                        try:
                            ext_resp = session.get(
                                lien_trouve,
                                timeout=7,
                                allow_redirects=True,
                                stream=True,
                                headers={"Referer": ""},
                            )
                            status = ext_resp.status_code

                            if status in [403, 401, 405]:
                                liens_externes_testes[lien_trouve] = 200
                            else:
                                liens_externes_testes[lien_trouve] = status

                            ext_resp.close()
                        except Exception:
                            liens_externes_testes[lien_trouve] = 0

            # --- NETTOYAGE DE LA PAGE. ---
            for element in soup(["nav", "footer", "aside"]):
                element.decompose()

            zone_principale = (
                soup.find("main")
                or soup.find("article")
                or soup.find("div", id="content")
                or soup.body
            )

            # --- CONTENU TEXTE. ---
            paragraphes_bruts = [
                p.text.strip() for p in zone_principale.find_all("p") if p.text.strip()
            ]
            paragraphes_uniques = list(dict.fromkeys(paragraphes_bruts))
            texte_principal = " ".join(paragraphes_uniques)

            nombre_mots = len(texte_principal.split())
            contient_lorem = "lorem ipsum" in texte_principal.lower()

            # --- TRAITEMENT DES IMAGES. ---
            images_trouvees = {}
            for img in zone_principale.find_all("img"):
                src = img.get("src")
                if not src:
                    continue

                if src.startswith("data:"):
                    continue

                lien_image = urljoin(url_courante, src)
                if lien_image in images_trouvees:
                    continue

                poids_octets = obtenir_poids_image(lien_image, session)

                img_data = {
                    "url": rendre_chemin_relatif(lien_image, domaine_cible),
                    "alt": img.get("alt", ""),
                    "poids_ko": round(poids_octets / 1024, 2)
                    if poids_octets > 0
                    else 0,
                }

                if img.get("width"):
                    img_data["w"] = img.get("width")
                if img.get("height"):
                    img_data["h"] = img.get("height")
                if poids_octets == -1:
                    img_data["erreur"] = True

                images_trouvees[lien_image] = img_data

            liste_images_propres = list(images_trouvees.values())

            # --- CONSTRUCTION DYNAMIQUE DU DICTIONNAIRE. ---

            # Bloc SEO.
            seo_data = {"titre": titre_onglet}
            if meta_description:
                seo_data["meta_desc"] = meta_description
            if hierarchie_titres:
                seo_data["titres_hn"] = hierarchie_titres

            # Bloc Contenu.
            contenu_data = {"mots": nombre_mots, "lorem_ipsum": contient_lorem}
            if plugins_trouves:
                contenu_data["plugins"] = list(plugins_trouves)

            # Bloc Liens.
            liens_data = {"internes": liens_internes_trouves}
            if liens_externes_testes:
                liens_data["externes"] = liens_externes_testes

            page_data = {
                "chemin": rendre_chemin_relatif(url_courante, domaine_cible),
                "architecture": {
                    "est_article": est_un_article,
                    "menus_nav": nombre_menus,
                    "contenu_mobile": a_contenu_mobile,
                },
                "seo": seo_data,
                "contenu": contenu_data,
                "liens": liens_data,
            }

            if liste_images_propres:
                page_data["images"] = liste_images_propres

            donnees_du_site.append(page_data)

            time.sleep(1)

        except Exception as e:
            log(f"Impossible de lire {url_courante} : {e}.")

    # --- STRUCTURATION DU JSON FINAL ---
    infos_globales = {
        "domaine": domaine_cible,
        "url_base": url_depart,
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "pages_visitees": len(urls_visitees),
        "pages_analysees": len(donnees_du_site),
        "theme_identite": {
            "presence_logo": site_a_logo,
            "presence_favicon": site_a_favicon,
        },
    }

    if plugins_globaux:
        infos_globales["plugins_globaux"] = list(plugins_globaux)

    dossier_audit = {"informations_globales": infos_globales, "pages": donnees_du_site}

    # --- SAUVEGARDE GLOBALE. ---
    os.makedirs("data", exist_ok=True)
    date_jour = datetime.now().strftime("%Y-%m-%d")
    nom_fichier = f"data/AUDIT_{nom_base}_{date_jour}.json"

    with open(nom_fichier, "w", encoding="utf-8") as f:
        json.dump(dossier_audit, f, indent=4, ensure_ascii=False)

    log(f"Terminé ! Stocké dans : {nom_fichier}.")

    return dossier_audit, nom_fichier


# --- LANCEMENT. ---
if __name__ == "__main__":
    url_test = "http://51.83.36.122/eg/demo-tc/"
    crawler_et_scraper(url_test)
