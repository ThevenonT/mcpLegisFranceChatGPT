# MCP Legifrance ChatGPT (PISTE)

Serveur MCP distant compatible Vercel/ChatGPT pour interroger l'API officielle Légifrance via PISTE.

## Variables d'environnement

- `PISTE_CLIENT_ID`
- `PISTE_CLIENT_SECRET`
- `PISTE_TOKEN_URL` (ex: `https://sandbox-oauth.piste.gouv.fr/api/oauth/token`)
- `LEGIFRANCE_BASE_URL` (URL de base fournie par PISTE / Swagger)
- `LEGIFRANCE_LODA_PATH` (optionnel, défaut `/consult/loda/search`)
- `LEGIFRANCE_CODE_PATH` (optionnel, défaut `/consult/code/search`)
- `LEGIFRANCE_JURI_PATH` (optionnel, défaut `/consult/juri/search`)

## Déploiement Vercel

1. Importer le repo dans Vercel.
2. Ajouter les variables d'environnement.
3. Déployer.
4. Ajouter l'URL `https://<ton-projet>.vercel.app/mcp` dans ChatGPT Developer Mode.

## Remarque importante

Les chemins d'API par défaut sont des valeurs de démarrage. Vérifie les endpoints exacts dans le Swagger PISTE de ton accès Légifrance et ajuste les variables `LEGIFRANCE_*_PATH` si nécessaire.
