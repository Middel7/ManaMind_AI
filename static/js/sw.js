/* ManaMind — service worker.
 *
 * Il existe d'abord pour une raison d'installation : Chrome sur Android ne
 * propose « Installer l'application » (et ne fabrique un vrai WebAPK) que si le
 * site declare un manifeste ET enregistre un service worker qui intercepte les
 * requetes. Sans lui, le menu se limite a « Creer un raccourci », qui n'est
 * qu'un marque-page deguise.
 *
 * Il reste volontairement mince. Le depot tient a ce qu'une page ne soit jamais
 * servie depuis un cache : une page a jour qui appelle un mm.js perime donne
 * des erreurs incomprehensibles. Donc reseau d'abord, toujours ; le cache n'est
 * qu'un filet pour le hors-ligne.
 */

const VERSION = 'mm-1';
const SHELL = `shell-${VERSION}`;

// Le socle du front, pour que la page hors-ligne garde la charte meme sans
// reseau. Les donnees, elles, viennent toutes de l'API : rien a precharger.
const SOCLE = [
  '/static/css/tokens.css',
  '/static/css/app.css',
  '/static/img/icon-192.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(SHELL)
      .then((cache) => cache.addAll(SOCLE))
      .then(() => self.skipWaiting())
      .catch(() => self.skipWaiting()),
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((noms) => Promise.all(
        noms.filter((nom) => nom !== SHELL).map((nom) => caches.delete(nom)),
      ))
      .then(() => self.clients.claim()),
  );
});

// Page de secours quand la navigation echoue faute de reseau. Elle est ecrite
// ici plutot que dans un fichier a part : c'est le seul ecran du site qui doive
// s'afficher sans que le serveur reponde.
function pageHorsLigne() {
  const html = `<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ManaMind — Hors ligne</title>
<link rel="stylesheet" href="/static/css/tokens.css">
<link rel="stylesheet" href="/static/css/app.css">
</head>
<body>
<div class="content">
  <div class="empty">
    <div class="stack-2" style="align-items:center">
      <p class="empty__title">Pas de connexion</p>
      <p class="empty__text">ManaMind lit votre collection et vos decks sur le
        serveur : sans réseau, il n'a rien à afficher. Réessayez dès que la
        connexion revient.</p>
    </div>
    <button class="btn btn--primary" onclick="location.reload()">Réessayer</button>
  </div>
</div>
</body>
</html>`;
  return new Response(html, {
    status: 200,
    headers: { 'Content-Type': 'text/html; charset=utf-8' },
  });
}

self.addEventListener('fetch', (event) => {
  const requete = event.request;
  if (requete.method !== 'GET') return;

  const url = new URL(requete.url);
  if (url.origin !== self.location.origin) return;

  // Navigation : le reseau fait foi, la page de secours prend le relais.
  if (requete.mode === 'navigate') {
    event.respondWith(fetch(requete).catch(() => pageHorsLigne()));
    return;
  }

  // Socle statique : reseau d'abord, copie de cote, cache en dernier recours.
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      fetch(requete)
        .then((reponse) => {
          if (reponse && reponse.ok) {
            const copie = reponse.clone();
            caches.open(SHELL).then((cache) => cache.put(requete, copie));
          }
          return reponse;
        })
        .catch(() => caches.match(requete).then((c) => c || Response.error())),
    );
    return;
  }

  // Tout le reste — l'API en premier lieu — part au reseau sans detour : une
  // reponse d'API servie depuis un cache serait une donnee fausse.
});
