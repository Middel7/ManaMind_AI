/**
 * ManaMind — socle applicatif partage par toutes les pages.
 *
 * Fournit : client API, session, coquille de navigation, formatage,
 * composants (toast, modale, autocompletion de cartes) et helpers d'images.
 *
 * Usage minimal dans une page :
 *   <div id="page"> ...contenu... </div>
 *   <script src="/static/js/mm.js"></script>
 *   <script>MM.boot({ title: 'Ma collection', nav: 'collection' });</script>
 */
(function (global) {
  'use strict';

  const MM = {};

  /* ══ Utilitaires ═══════════════════════════════════════════════════════ */

  const esc = (value) => String(value ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');

  const el = (sel, root) => (root || document).querySelector(sel);
  const els = (sel, root) => Array.from((root || document).querySelectorAll(sel));

  function node(html) {
    const tpl = document.createElement('template');
    tpl.innerHTML = html.trim();
    return tpl.content.firstElementChild;
  }

  function debounce(fn, wait) {
    let timer;
    return function (...args) {
      clearTimeout(timer);
      timer = setTimeout(() => fn.apply(this, args), wait);
    };
  }

  MM.esc = esc;
  MM.el = el;
  MM.els = els;
  MM.node = node;
  MM.debounce = debounce;

  /* ══ Client API ════════════════════════════════════════════════════════ */

  /** Message lisible a partir d'un corps d'erreur.
   *  FastAPI renvoie ses erreurs de validation sous forme de liste d'objets :
   *  passee telle quelle a Error(), elle s'affichait « [object Object] ». */
  function errorText(data) {
    if (!data) return '';
    const detail = data.error ?? data.detail;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) {
      return detail.map((item) => {
        if (typeof item === 'string') return item;
        const field = Array.isArray(item.loc) ? item.loc.slice(1).join('.') : '';
        return field ? `${field} : ${item.msg}` : (item.msg || '');
      }).filter(Boolean).join(' · ');
    }
    if (detail && typeof detail === 'object') return detail.msg || JSON.stringify(detail);
    return '';
  }

  async function request(method, url, body) {
    const options = { method, headers: {}, credentials: 'same-origin' };
    if (body !== undefined) {
      options.headers['Content-Type'] = 'application/json';
      options.body = JSON.stringify(body);
    }

    let response;
    try {
      response = await fetch(url, options);
    } catch (err) {
      throw new Error('Connexion au serveur impossible.');
    }

    if (response.status === 401) {
      sessionStorage.removeItem('_mm_user');
      const next = encodeURIComponent(location.pathname + location.search);
      location.href = '/login?next=' + next;
      throw new Error('Session expirée');
    }

    let data = null;
    const type = response.headers.get('content-type') || '';
    if (type.includes('application/json')) {
      data = await response.json().catch(() => null);
    }

    // 405 : la route existe dans le code mais pas dans le serveur qui tourne.
    // Le message par defaut (« Method Not Allowed ») n'aide en rien.
    if (response.status === 405) {
      throw new Error('Cette action n’est pas disponible sur le serveur en cours '
        + "d'exécution. Redémarrez-le pour charger la dernière version.");
    }

    if (!response.ok) {
      throw new Error(errorText(data) || `Erreur ${response.status}`);
    }
    return data;
  }

  MM.api = {
    get: (url) => request('GET', url),
    post: (url, body) => request('POST', url, body ?? {}),
    put: (url, body) => request('PUT', url, body ?? {}),
    patch: (url, body) => request('PATCH', url, body ?? {}),
    del: (url) => request('DELETE', url),

    /** Construit une querystring en omettant les valeurs vides. */
    qs(params) {
      const search = new URLSearchParams();
      Object.entries(params || {}).forEach(([key, value]) => {
        if (value === undefined || value === null || value === '') return;
        if (Array.isArray(value)) {
          if (value.length) search.set(key, value.join(','));
        } else {
          search.set(key, value);
        }
      });
      const out = search.toString();
      return out ? '?' + out : '';
    },
  };

  /* ══ Session ═══════════════════════════════════════════════════════════ */

  const USER_KEY = '_mm_user';

  MM.session = {
    cached() {
      try { return JSON.parse(sessionStorage.getItem(USER_KEY)); } catch { return null; }
    },

    async load() {
      const data = await MM.api.get('/auth/me');
      if (!data || !data.authenticated) {
        sessionStorage.removeItem(USER_KEY);
        const next = encodeURIComponent(location.pathname + location.search);
        location.href = '/login?next=' + next;
        return null;
      }
      sessionStorage.setItem(USER_KEY, JSON.stringify(data.user));
      return data.user;
    },

    async logout() {
      sessionStorage.removeItem(USER_KEY);
      try { await MM.api.post('/auth/logout'); } catch { /* deconnexion locale suffit */ }
      location.href = '/login';
    },
  };

  /* ══ Formatage ═════════════════════════════════════════════════════════ */

  const nfInt = new Intl.NumberFormat('fr-FR');
  const nfEur = new Intl.NumberFormat('fr-FR', {
    style: 'currency', currency: 'EUR', maximumFractionDigits: 2,
  });

  MM.fmt = {
    int: (value) => nfInt.format(Math.round(Number(value) || 0)),

    /** Un nombre a virgule, ecrit a la francaise. */
    dec(value, digits = 2) {
      const amount = Number(value);
      if (!isFinite(amount)) return '—';
      return amount.toLocaleString('fr-FR', {
        minimumFractionDigits: digits, maximumFractionDigits: digits,
      });
    },

    eur(value, { compact = false } = {}) {
      if (value === null || value === undefined) return '—';
      const amount = Number(value);
      if (compact && amount >= 1000) {
        return nfEur.format(Math.round(amount)).replace(/,00/, '');
      }
      return nfEur.format(amount);
    },

    /** Pourcentage a la francaise : virgule decimale, et pas de decimale
     *  inutile. toFixed() ecrivait « 0.0 % » au milieu d'une page en francais. */
    pct(value, digits = 1) {
      const amount = Number(value);
      if (!isFinite(amount)) return '—';
      const rounded = Number(amount.toFixed(digits));
      return `${rounded.toLocaleString('fr-FR', { maximumFractionDigits: digits })} %`;
    },

    /** "il y a 3 jours", "aujourd'hui", "il y a 2 mois" */
    since(iso) {
      if (!iso) return null;
      const then = new Date(iso);
      if (isNaN(then)) return null;
      const days = Math.floor((Date.now() - then.getTime()) / 86400000);
      if (days <= 0) return "aujourd'hui";
      if (days === 1) return 'hier';
      if (days < 30) return `il y a ${days} jours`;
      const months = Math.floor(days / 30);
      if (months < 12) return `il y a ${months} mois`;
      const years = Math.floor(months / 12);
      return years === 1 ? 'il y a un an' : `il y a ${years} ans`;
    },

    date(iso) {
      if (!iso) return '—';
      const value = new Date(iso);
      if (isNaN(value)) return '—';
      return value.toLocaleDateString('fr-FR', { day: 'numeric', month: 'short', year: 'numeric' });
    },

    /** Accorde un nom commun sur son nombre. */
    plural: (count, one, many) => `${nfInt.format(count)} ${count > 1 ? (many || one + 's') : one}`,

    finish(value) {
      return { foil: 'Foil', etched: 'Gravé' }[value] || null;
    },

    rarity(value) {
      return {
        common: 'Commune', uncommon: 'Peu commune',
        rare: 'Rare', mythic: 'Mythique', special: 'Spéciale', bonus: 'Bonus',
      }[value] || value || '';
    },
  };

  /* ══ Images de cartes ══════════════════════════════════════════════════ */

  MM.img = {
    /** L'illustration seule, cadrage panoramique — pour les visuels de fond. */
    art(item) {
      const src = item && (item.image_normal || item.image_small);
      if (!src) return null;
      return src.replace('/normal/', '/art_crop/').replace('/small/', '/art_crop/');
    },

    card(item, size) {
      if (!item) return null;
      if (size === 'small') return item.image_small || item.image_normal || null;
      return item.image_normal || item.image_small || null;
    },

    /** Vignette de carte, avec repli lisible quand l'image manque. */
    frame(item, { size = 'normal', lazy = true } = {}) {
      const src = MM.img.card(item, size);
      if (src) {
        return `<img src="${esc(src)}" alt="${esc(item.card_name || '')}"
                  ${lazy ? 'loading="lazy" decoding="async"' : ''}>`;
      }
      return `<div class="mtg-card__fallback">
                <span class="strong">${esc(item.card_name || 'Carte inconnue')}</span>
                <span class="xs dim">Illustration indisponible</span>
              </div>`;
    },

    pips(colors) {
      const list = (colors && colors.length) ? colors : ['C'];
      return `<span class="pips">${list
        .map((c) => `<span class="pip pip--${esc(c)}" title="${esc(c)}"></span>`)
        .join('')}</span>`;
    },
  };

  /* ══ Notifications ═════════════════════════════════════════════════════ */

  function toastHost() {
    let host = el('.toasts');
    if (!host) {
      host = node('<div class="toasts" role="status" aria-live="polite"></div>');
      document.body.appendChild(host);
    }
    return host;
  }

  MM.toast = function (message, kind = 'info', ms = 3600) {
    const item = node(`<div class="toast toast--${esc(kind)}">
        <span class="toast__mark"></span><span>${esc(message)}</span>
      </div>`);
    toastHost().appendChild(item);
    setTimeout(() => {
      item.classList.add('toast--leaving');
      setTimeout(() => item.remove(), 250);
    }, ms);
    return item;
  };

  MM.toast.ok = (message) => MM.toast(message, 'ok');
  MM.toast.error = (message) => MM.toast(message, 'error', 5200);

  /* ══ Modale ════════════════════════════════════════════════════════════ */

  MM.modal = function ({ title, body, footer, wide = false, onClose }) {
    const overlay = node(`
      <div class="modal" role="dialog" aria-modal="true">
        <div class="modal__box ${wide ? 'modal__box--wide' : ''}">
          <div class="modal__head">
            <h2 class="h3">${esc(title || '')}</h2>
            <button class="btn btn--ghost btn--icon" data-close aria-label="Fermer">
              ${MM.icons.close}
            </button>
          </div>
          <div class="modal__body"></div>
          ${footer ? '<div class="modal__foot"></div>' : ''}
        </div>
      </div>`);

    const bodyHost = el('.modal__body', overlay);
    if (typeof body === 'string') bodyHost.innerHTML = body;
    else if (body) bodyHost.appendChild(body);

    if (footer) {
      const footHost = el('.modal__foot', overlay);
      if (typeof footer === 'string') footHost.innerHTML = footer;
      else footHost.appendChild(footer);
    }

    function close() {
      document.removeEventListener('keydown', onKey);
      overlay.remove();
      if (onClose) onClose();
    }

    function onKey(event) { if (event.key === 'Escape') close(); }

    overlay.addEventListener('click', (event) => {
      if (event.target === overlay || event.target.closest('[data-close]')) close();
    });
    document.addEventListener('keydown', onKey);
    document.body.appendChild(overlay);

    const focusable = overlay.querySelector('input, button:not([data-close]), select, textarea');
    if (focusable) focusable.focus();

    return { root: overlay, body: bodyHost, close };
  };

  MM.confirm = function ({ title, text, confirmLabel = 'Confirmer', danger = false }) {
    return new Promise((resolve) => {
      let settled = false;
      const dialog = MM.modal({
        title,
        body: `<p class="muted">${esc(text)}</p>`,
        footer: `
          <button class="btn" data-cancel>Annuler</button>
          <button class="btn ${danger ? 'btn--danger' : 'btn--primary'}" data-ok>
            ${esc(confirmLabel)}
          </button>`,
        onClose: () => { if (!settled) { settled = true; resolve(false); } },
      });
      dialog.root.addEventListener('click', (event) => {
        if (event.target.closest('[data-ok]')) { settled = true; resolve(true); dialog.close(); }
        if (event.target.closest('[data-cancel]')) { settled = true; resolve(false); dialog.close(); }
      });
    });
  };

  // Fenetre de retour, ouverte par le bouton du coin bas droit. Le message part
  // avec le compte connecte : rien a saisir d'autre que le texte, et l'ecran
  // d'administration sait de qui il vient.
  MM.feedbackForm = function () {
    const dialog = MM.modal({
      title: 'Aidez-moi à améliorer ManaMind',
      body: `
        <div class="stack">
          <p class="muted small">Ce qui vous manque, ce qui vous gêne, ce que vous
            aimeriez voir&nbsp;: écrivez-le ici, je le lis.</p>
          <label class="field">
            <span class="sr-only">Votre message</span>
            <textarea class="textarea textarea--prose" id="mmFeedbackText" rows="6"
              maxlength="4000"
              placeholder="Par exemple : j'aimerais pouvoir trier ma collection par extension."></textarea>
          </label>
        </div>`,
      footer: `
        <button class="btn" data-close>Annuler</button>
        <button class="btn btn--primary" data-send>Envoyer</button>`,
    });

    const champ = el('#mmFeedbackText', dialog.root);
    const bouton = el('[data-send]', dialog.root);
    champ.focus();

    bouton.addEventListener('click', async () => {
      const message = champ.value.trim();
      if (!message) { champ.focus(); return MM.toast.error("Écrivez quelque chose avant d'envoyer."); }
      // Le bouton se verrouille le temps de l'envoi : un double clic creerait
      // deux fois le meme retour.
      bouton.disabled = true;
      try {
        await MM.api.post('/api/feedback', { message });
        dialog.close();
        MM.toast.ok('Merci, votre message est bien arrivé.');
      } catch (error) {
        bouton.disabled = false;
        MM.toast.error(error.message || 'Envoi impossible.');
      }
    });
  };

  /* ══ Icones ════════════════════════════════════════════════════════════ */

  const svg = (paths, extra = '') => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
    stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" ${extra}>${paths}</svg>`;

  MM.icons = {
    home: svg('<path d="M3 10.5 12 3l9 7.5"/><path d="M5 9.5V21h14V9.5"/>'),
    collection: svg('<rect x="3" y="3" width="7" height="9" rx="1.4"/><rect x="14" y="3" width="7" height="9" rx="1.4"/><rect x="3" y="15" width="7" height="6" rx="1.4"/><rect x="14" y="15" width="7" height="6" rx="1.4"/>'),
    decks: svg('<rect x="7" y="3" width="12" height="16" rx="2"/><path d="M4 6v13a2 2 0 0 0 2 2h10"/>'),
    plus: svg('<path d="M12 5v14M5 12h14"/>'),
    minus: svg('<path d="M5 12h14"/>'),
    search: svg('<circle cx="11" cy="11" r="7"/><path d="m20 20-3.6-3.6"/>'),
    upload: svg('<path d="M12 16V4"/><path d="m7 9 5-5 5 5"/><path d="M4 16v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3"/>'),
    booster: svg('<path d="M4 7h16l-1.2 13.2a1 1 0 0 1-1 .8H6.2a1 1 0 0 1-1-.8Z"/><path d="M9 7V4.5A2.5 2.5 0 0 1 11.5 2h1A2.5 2.5 0 0 1 15 4.5V7"/>'),
    sparkle: svg('<path d="m12 3 1.9 5.6L19.5 10l-5.6 1.9L12 17.5l-1.9-5.6L4.5 10l5.6-1.4Z"/><path d="M18.5 16.5 19 18l1.5.5L19 19l-.5 1.5L18 19l-1.5-.5L18 18Z"/>'),
    swap: svg('<path d="M4 8h13l-3-3"/><path d="M20 16H7l3 3"/>'),
    move: svg('<path d="M12 3v18M3 12h18"/><path d="m8 7 4-4 4 4M8 17l4 4 4-4M7 8l-4 4 4 4M17 8l4 4-4 4"/>'),
    chart: svg('<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>'),
    crown: svg('<path d="M3 8l3.5 3L12 4l5.5 7L21 8l-2 11H5Z"/>'),
    user: svg('<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>'),
    close: svg('<path d="M6 6l12 12M18 6 6 18"/>'),
    menu: svg('<path d="M4 7h16M4 12h16M4 17h16"/>'),
    check: svg('<path d="m4 12 5 5L20 6"/>'),
    chevron: svg('<path d="m9 6 6 6-6 6"/>'),
    back: svg('<path d="m15 6-6 6 6 6"/>'),
    trash: svg('<path d="M4 7h16"/><path d="M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/><path d="M6 7l1 13a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1l1-13"/>'),
    edit: svg('<path d="M4 20h4l10-10-4-4L4 16Z"/><path d="m13.5 6.5 4 4"/>'),
    external: svg('<path d="M14 4h6v6"/><path d="M20 4 10 14"/><path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>'),
    logout: svg('<path d="M9 21H5a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h4"/><path d="M16 17l5-5-5-5"/><path d="M21 12H9"/>'),
    refresh: svg('<path d="M20 11a8 8 0 1 0-1.6 5.6"/><path d="M20 5v6h-6"/>'),
    warning: svg('<path d="M12 4 2.5 20h19Z"/><path d="M12 10v4"/><path d="M12 17.2v.1"/>'),
    box: svg('<path d="m12 3 8 4.5v9L12 21l-8-4.5v-9Z"/><path d="M12 21v-9"/><path d="m4 7.5 8 4.5 8-4.5"/>'),
    shield: svg('<path d="M12 3 5 6v6c0 4.2 2.9 7.6 7 9 4.1-1.4 7-4.8 7-9V6Z"/>'),
    info: svg('<circle cx="12" cy="12" r="9"/><path d="M12 11v5"/><path d="M12 8v.1"/>'),
    eye: svg('<path d="M2 12s3.6-6 10-6 10 6 10 6-3.6 6-10 6-10-6-10-6Z"/><circle cx="12" cy="12" r="2.6"/>'),
    tag: svg('<path d="M3 12V4h8l9 9-8 8Z"/><path d="M7.5 7.5v.1"/>'),
    coin: svg('<circle cx="12" cy="12" r="9"/><path d="M15 9.5A3 3 0 0 0 9 11c0 2.5 6 1.5 6 4a3 3 0 0 1-6-1.5"/>'),
    chat: svg('<path d="M20 15a2 2 0 0 1-2 2H8l-4 4V5a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2Z"/>'),
    layers: svg('<path d="m12 3 9 5-9 5-9-5Z"/><path d="m3 13 9 5 9-5"/>'),
  };

  /* ══ Coquille de navigation ════════════════════════════════════════════ */

  const NAV_GROUPS = [
    {
      items: [
        { key: 'home', label: 'Accueil', href: '/', icon: 'home' },
        { key: 'collection', label: 'Ma collection', href: '/collection', icon: 'collection' },
        { key: 'decks', label: 'Mes decks', href: '/decks', icon: 'decks' },
      ],
    },
    {
      label: 'Gérer ma collection',
      items: [
        { key: 'add', label: 'Ajouter des cartes à ma collection',
          href: '/collection/ajout', icon: 'plus' },
        { key: 'import',
          label: 'Importer une liste de cartes pour ma collection ou une decklist',
          href: '/collection/import', icon: 'upload' },
        { key: 'boosters', label: "Sélectionner les extensions que j'ai ouvertes",
          href: '/collection/boosters', icon: 'booster' },
      ],
    },
    {
      label: 'Analyser',
      items: [
        { key: 'build', label: 'Construire un deck avec mes cartes disponibles',
          href: '/collection/commandants', icon: 'crown' },
        { key: 'improve', label: 'Améliorer mon deck avec les cartes de ma collection',
          href: '/decks/ameliorer', icon: 'sparkle' },
        { key: 'analyze', label: 'Améliorer mon deck grâce aux suggestions de ManaMind',
          href: '/decks/analyse', icon: 'chart' },
        { key: 'card', label: 'Trouver un commandant pour une carte',
          href: '/cartes/commandant', icon: 'search' },
        { key: 'moves', label: 'Cartes à changer de deck',
          href: '/decks/deplacements', icon: 'move' },
        { key: 'swap', label: 'Trouver un nouveau commandant pour mon deck',
          href: '/decks/commandant', icon: 'swap' },
      ],
    },
  ];

  const TABS = [
    { key: 'home', label: 'Accueil', href: '/', icon: 'home' },
    { key: 'collection', label: 'Collection', href: '/collection', icon: 'collection' },
    { key: 'add', label: 'Ajouter', href: '/collection/ajout', icon: 'plus', primary: true },
    { key: 'decks', label: 'Decks', href: '/decks', icon: 'decks' },
    { key: 'profile', label: 'Profil', href: '/profil', icon: 'user' },
  ];

  function renderSidebar(active, user) {
    const groups = NAV_GROUPS.map((group) => `
      <div class="nav__group">
        ${group.label ? `<div class="nav__label">${esc(group.label)}</div>` : ''}
        ${group.items.map((item) => `
          <a class="nav__item" href="${item.href}"
             ${item.key === active ? 'aria-current="page"' : ''}>
            ${MM.icons[item.icon]}<span>${esc(item.label)}</span>
          </a>`).join('')}
      </div>`).join('');

    const initials = (user && (user.display_name || user.email) || '?')
      .trim().slice(0, 2).toUpperCase();

    return `
      <a class="brand" href="/">
        <span class="brand__mark">${MM.icons.sparkle}</span>
        <span class="brand__name">ManaMind</span>
      </a>
      <nav class="nav" aria-label="Navigation principale">${groups}</nav>
      <div class="sidebar__foot">
        ${user && user.role === 'admin'
          ? `<a class="nav__item" href="/admin">${MM.icons.shield}<span>Administration</span></a>`
          : ''}
        <a class="user-chip" href="/profil">
          <span class="avatar" id="mmAvatar">${esc(initials)}</span>
          <span class="grow truncate">
            <span class="small strong truncate" style="display:block">
              ${esc((user && (user.display_name || user.email)) || '')}
            </span>
            <span class="xs dim">Voir mon profil</span>
          </span>
        </a>
        <button class="nav__item" id="mmLogout" type="button">
          ${MM.icons.logout}<span>Déconnexion</span>
        </button>
      </div>`;
  }

  function renderTabs(active) {
    return TABS.map((tab) => {
      if (tab.primary) {
        return `<a class="tab tab--add" href="${tab.href}" aria-label="${esc(tab.label)}">
                  <span>${MM.icons.plus}</span><em>${esc(tab.label)}</em>
                </a>`;
      }
      return `<a class="tab" href="${tab.href}" ${tab.key === active ? 'aria-current="page"' : ''}>
                ${MM.icons[tab.icon]}<span>${esc(tab.label)}</span>
              </a>`;
    }).join('');
  }

  /**
   * Construit la coquille autour de #page et charge la session.
   * @returns {Promise<object|null>} l'utilisateur connecte
   */
  MM.boot = async function ({ title = '', nav = '', actions = '', back = null } = {}) {
    const page = el('#page');
    if (!page) throw new Error('MM.boot : #page introuvable');

    // Des l'entree, avant toute requete : le numero de version doit s'afficher
    // meme si la session ou les donnees echouent — c'est souvent la qu'on veut
    // savoir quelle version on regarde.
    MM.showVersion();

    // La barre du haut n'affiche plus le titre de l'ecran : il repetait
    // l'entree de menu active et le titre de la page juste en dessous. Il ne
    // sert plus qu'a nommer l'onglet du navigateur, si la page ne l'a pas fait.
    if (title && !document.title) document.title = `ManaMind — ${title}`;

    const user = MM.session.cached();

    const shell = node(`
      <div class="app">
        <aside class="sidebar" id="mmSidebar"></aside>
        <div class="main">
          <header class="topbar">
            <button class="btn btn--ghost btn--icon topbar__burger" id="mmBurger"
                    aria-label="Ouvrir la navigation">${MM.icons.menu}</button>
          </header>
        </div>
      </div>
      `);

    document.body.insertBefore(shell, page);
    el('.main', shell).appendChild(page);
    page.classList.add('content');

    // Le retour et les actions d'ecran vivaient dans la barre du haut, ce qui
    // obligeait a la garder sur les pages qui en ont. Places dans le contenu,
    // ils permettent a la barre de disparaitre partout sur grand ecran.
    if (back || actions) {
      page.insertAdjacentHTML('afterbegin', `
        <div class="page-head">
          ${back ? `<a class="btn btn--ghost btn--icon" href="${esc(back)}"
                       aria-label="Retour">${MM.icons.back}</a>` : ''}
          <div class="topbar__actions" id="mmTopActions">${actions}</div>
        </div>`);
    }

    // Coin bas droit : l'appel a retour partout, le lien de soutien partout
    // sauf sur l'accueil — celui-ci porte deja le bandeau, les chiffres et le
    // parcours d'installation, et n'a pas besoin d'une sollicitation de plus.
    // Le lien est nu, sans script ni image tierce : la page n'emet aucune
    // requete vers Ko-fi tant qu'on ne clique pas.
    const dock = node('<div class="dock"></div>');
    dock.appendChild(node(`
      <button class="support-tag" type="button" id="mmFeedback"
              aria-label="Help me improve" title="Help me improve"
              >${MM.icons.chat}<span>Help me improve</span></button>`));
    if (nav !== 'home') {
      dock.appendChild(node(`
        <a class="support-tag" href="https://ko-fi.com/middel7"
           target="_blank" rel="noopener noreferrer"
           aria-label="Support the project — buy me a coffee"
           title="Support the project — buy me a coffee"
           >${MM.icons.coin}<span>Support the project &mdash; buy me a coffee</span></a>`));
    }
    el('#mmFeedback', dock).addEventListener('click', () => MM.feedbackForm());
    document.body.appendChild(dock);

    const bottombar = node(`<nav class="bottombar" aria-label="Navigation">${renderTabs(nav)}</nav>`);
    document.body.appendChild(bottombar);

    const sidebar = el('#mmSidebar', shell);
    sidebar.innerHTML = renderSidebar(nav, user);

    // Tiroir sur petit ecran
    let scrim = null;
    const closeDrawer = () => {
      sidebar.dataset.open = 'false';
      if (scrim) { scrim.remove(); scrim = null; }
    };
    el('#mmBurger', shell).addEventListener('click', () => {
      if (sidebar.dataset.open === 'true') return closeDrawer();
      sidebar.dataset.open = 'true';
      scrim = node('<div class="scrim"></div>');
      scrim.addEventListener('click', closeDrawer);
      document.body.appendChild(scrim);
    });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeDrawer(); });

    const bindLogout = () => {
      const button = el('#mmLogout');
      if (button) button.addEventListener('click', () => MM.session.logout());
    };
    bindLogout();

    // Verification reseau : rafraichit l'affichage si la session a change
    const fresh = await MM.session.load();
    if (fresh && (!user || fresh.display_name !== user.display_name || fresh.role !== user.role)) {
      sidebar.innerHTML = renderSidebar(nav, fresh);
      bindLogout();
    }
    if (fresh) MM.decorateAvatar(fresh);
    return fresh;
  };

  /** Numero de version, en haut a droite : dit d'un coup d'oeil si la page
   *  qu'on regarde vient bien du dernier deploiement. */
  MM.showVersion = async function () {
    if (el('#mmVersion')) return;
    try {
      const v = await MM.api.get('/api/version');
      const tag = node(`<a class="version-tag" id="mmVersion" href="/api/version"
        target="_blank" rel="noopener"
        title="${esc(v.subject || '')} — ${esc(v.sha || '')} du ${esc((v.date || '').slice(0, 10))}"
        >v${v.build}</a>`);
      document.body.appendChild(tag);
    } catch { /* l'absence de version n'empeche rien */ }
  };

  /** Remplace les initiales par la carte fetiche du profil, si elle existe. */
  MM.decorateAvatar = async function () {
    try {
      const data = await MM.api.get('/api/profile');
      const avatar = el('#mmAvatar');
      const art = data.profile && data.profile.avatar_scryfall_id;
      if (avatar && art) {
        avatar.innerHTML = `<img src="${MM.scryfallArt(art)}" alt="">`;
      }
      MM.profile = data.profile;
    } catch { /* l'avatar en initiales reste valable */ }
  };

  /** URL de l'illustration a partir d'un identifiant Scryfall. */
  MM.scryfallArt = function (scryfallId, kind = 'art_crop') {
    if (!scryfallId) return '';
    const a = scryfallId[0];
    const b = scryfallId[1];
    return `https://cards.scryfall.io/${kind}/front/${a}/${b}/${scryfallId}.jpg`;
  };

  /* ══ Autocompletion de cartes ══════════════════════════════════════════ */

  /**
   * Attache une autocompletion de noms de cartes a un champ texte.
   * @param {HTMLInputElement} input
   * @param {(card:object)=>void} onPick
   */
  MM.autocomplete = function (input, onPick, { minChars = 2 } = {}) {
    const wrap = input.closest('.autocomplete') || input.parentElement;
    wrap.classList.add('autocomplete');
    let list = null;
    let items = [];
    let cursor = -1;

    function close() {
      if (list) { list.remove(); list = null; }
      items = []; cursor = -1;
    }

    function open(results) {
      close();
      if (!results.length) return;
      items = results;
      list = node('<div class="autocomplete__list" role="listbox"></div>');
      results.forEach((card, index) => {
        const image = card.image_small || card.image_normal;
        const button = node(`
          <button type="button" class="autocomplete__item" role="option" data-index="${index}">
            ${image ? `<img src="${esc(image)}" alt="" loading="lazy">` : ''}
            <span class="grow truncate">${esc(card.name || card.card_name)}</span>
            ${card.set_code ? `<span class="xs dim">${esc(String(card.set_code).toUpperCase())}</span>` : ''}
          </button>`);
        button.addEventListener('click', () => { onPick(card); close(); });
        list.appendChild(button);
      });
      wrap.appendChild(list);
    }

    function highlight(next) {
      if (!list) return;
      const options = els('.autocomplete__item', list);
      if (!options.length) return;
      cursor = (next + options.length) % options.length;
      options.forEach((option, index) =>
        option.setAttribute('aria-selected', index === cursor ? 'true' : 'false'));
      options[cursor].scrollIntoView({ block: 'nearest' });
    }

    const search = debounce(async (term) => {
      try {
        const data = await MM.api.get(
          `/api/v2/cards/suggest?q=${encodeURIComponent(term)}&limit=8`);
        open((data && data.cards) || []);
      } catch { close(); }
    }, 180);

    input.addEventListener('input', () => {
      const term = input.value.trim();
      if (term.length < minChars) return close();
      search(term);
    });

    input.addEventListener('keydown', (event) => {
      if (!list) return;
      if (event.key === 'ArrowDown') { event.preventDefault(); highlight(cursor + 1); }
      else if (event.key === 'ArrowUp') { event.preventDefault(); highlight(cursor - 1); }
      else if (event.key === 'Enter' && cursor >= 0) {
        event.preventDefault();
        onPick(items[cursor]);
        close();
      } else if (event.key === 'Escape') close();
    });

    document.addEventListener('click', (event) => {
      if (!wrap.contains(event.target)) close();
    });

    return { close };
  };

  /* ══ Liens marchands ═══════════════════════════════════════════════════ */

  MM.market = {
    /** RELIC-TRADE : proposer la carte a la vente. */
    sell: (name) =>
      'https://relictrade.gg/en/recherche?q=' + encodeURIComponent(name || ''),

    /** Cardmarket : offres d'achat. Aucune URL de fiche n'est stockee en base,
     *  la recherche par nom est donc le seul lien qui ne casse jamais. */
    buy: (name) =>
      'https://www.cardmarket.com/fr/Magic/Products/Search?searchString='
      + encodeURIComponent(name || ''),
  };

  /* ══ Rendu de cartes ═══════════════════════════════════════════════════ */

  /* ══ Vignette de carte ═════════════════════════════════════════════════
   *
   * Une seule vignette pour tout le projet : meme taille, memes informations,
   * memes gestes. Chaque ecran n'y ajoute que ses propres boutons.
   *
   * Gestes uniformes, partout :
   *   - un clic sur l'illustration ou sur le nom ouvre la fiche de la carte ;
   *   - « Acheter » et « Vendre » sont toujours proposes ;
   *   - « + » et « − » reglent le deck courant sur un ecran de deck, la
   *     collection ailleurs — un libelle le dit sous les boutons.
   */

  /** Deck vise par les reglages des vignettes. Pose par les ecrans de deck. */
  MM.deckContext = null;

  /** Ce qu'une API peut nommer de dix facons, ramene a un seul jeu de champs. */
  function cardFacts(item) {
    const owned = item.owned ?? item.quantity ?? item.copies_owned ?? 0;
    const used = item.used
      ?? (item.in_decks ? item.in_decks.length : undefined)
      ?? item.copies_used ?? 0;
    return {
      name: item.card_name || item.name || '',
      owned: Number(owned) || 0,
      used: Number(used) || 0,
      free: item.free != null ? Number(item.free) : Math.max(0, (Number(owned) || 0) - (Number(used) || 0)),
      price: item.unit_price != null ? item.unit_price : (item.low_price ?? null),
      decks: item.in_decks || [],
    };
  }

  /**
   * Vignette de carte, unique pour tout le projet.
   *
   * @param {object} item     carte, sous n'importe quelle forme d'API
   * @param {object} options
   *   context {'deck'|'collection'|null} ce que reglent « + » et « − »
   *   deckQty {number}   exemplaires dans le deck courant, pour le reglage deck
   *   actions {Array}    boutons propres a l'ecran : { label, act, variant, title, done }
   *   note    {string}   mention de l'ecran (frequence, score, motif…)
   *   muted   {boolean}  vignette grisee
   *   market  {boolean}  proposer l'achat et la vente (vrai par defaut)
   */
  MM.cardTile = function (item, options = {}) {
    const { context = 'collection', deckQty = null, actions = [],
            note = '', muted = false, market = true } = options;
    const facts = cardFacts(item);
    const finish = MM.fmt.finish(item.finish);

    // Le nombre de decks ne se pose plus sur l'illustration : la ligne sous la
    // carte dit deja ce qui est possede et ce qui reste libre.
    const badges = [];
    if (item.game_changer) {
      badges.push('<span class="badge badge--warn" title="Carte à fort impact">GC</span>');
    }
    if (options.flag) {
      badges.push(`<span class="badge badge--ok"
        ${options.flagTitle ? `title="${esc(options.flagTitle)}"` : ''}
        >${esc(options.flag)}</span>`);
    }

    // Reglage : le deck quand on est sur un ecran de deck, la collection sinon.
    const stepper = (kind, value, label, hint) => `
      <span class="mtg-card__ops">
        <span class="stepper">
          <button data-mm="${kind}-dec" data-card="${esc(facts.name)}"
                  ${item.id ? `data-item="${esc(item.id)}"` : ''}
                  aria-label="Retirer un exemplaire ${hint}">−</button>
          <span class="stepper__value">${MM.fmt.int(value)}</span>
          <button data-mm="${kind}-inc" data-card="${esc(facts.name)}"
                  ${item.id ? `data-item="${esc(item.id)}"` : ''}
                  aria-label="Ajouter un exemplaire ${hint}">+</button>
        </span>
        <span class="xs dim">${label}</span>
      </span>`;

    return `
      <article class="mtg-card ${muted ? 'mtg-card--muted' : ''}"
               data-id="${esc(item.id ?? '')}" data-name="${esc(facts.name)}">
        <div class="mtg-card__frame"
             ${options.frameLink ? `data-href="${esc(options.frameLink)}"` : ''}
             ${options.frameTitle ? `title="${esc(options.frameTitle)}"` : ''}>
          ${MM.img.frame({ ...item, card_name: facts.name })}
          ${badges.length ? `<div class="mtg-card__badges">${badges.join('')}</div>` : ''}
        </div>
        <div class="mtg-card__foot">
          <span class="mtg-card__name" title="${esc(facts.name)}">${esc(facts.name)}</span>

          <span class="mtg-card__meta">
            ${item.set_code ? `<span>${esc(item.set_code)}</span>` : ''}
            ${finish ? `<span class="accent">${esc(finish)}</span>` : ''}
            ${note ? `<span class="dim">${note}</span>` : ''}
            ${facts.price != null
              ? `<span class="mtg-card__price">${MM.fmt.eur(facts.price)}</span>` : ''}
          </span>

          <span class="mtg-card__owned">
            ${facts.owned
              ? `<span title="${facts.owned} exemplaire${facts.owned > 1 ? 's' : ''} en collection"
                  ><span class="strong">${MM.fmt.int(facts.owned)}</span> en collection</span>
                 <span class="${facts.free ? 'accent' : 'dim'}"
                       title="${facts.free
                         ? `${facts.free} qu'aucun deck n'utilise`
                         : 'tous les exemplaires sont engagés dans des decks'}"
                   >${facts.free ? `${MM.fmt.int(facts.free)} libre${facts.free > 1 ? 's' : ''}`
                                 : 'aucune libre'}</span>`
              : '<span class="dim">absente de la collection</span>'}
          </span>

          ${context === 'deck'
            ? stepper('deck', deckQty ?? 0, 'dans le deck', 'de ce deck')
            : (context === 'collection'
              ? stepper('coll', facts.owned, 'en collection', 'de ma collection')
              : '')}

          <span class="mtg-card__bottom">
          ${market ? `
            <span class="mtg-card__market">
              <a class="btn btn--sm" target="_blank" rel="noopener"
                 href="${esc(MM.market.buy(facts.name))}"
                 title="Voir les offres d'achat de ${esc(facts.name)} sur Cardmarket"
                >Acheter</a>
              <a class="btn btn--sm" target="_blank" rel="noopener"
                 href="${esc(MM.market.sell(facts.name))}"
                 title="Proposer ${esc(facts.name)} à la vente sur RELIC-TRADE"
                >Vendre</a>
            </span>` : ''}

          ${actions.length ? `
            <span class="mtg-card__actions">
              ${actions.map((action) => `
                <button class="btn btn--sm ${action.variant ? `btn--${action.variant}` : ''}
                               ${action.done ? 'is-done' : ''}"
                        data-act="${esc(action.act)}" data-card="${esc(facts.name)}"
                        ${action.title ? `title="${esc(action.title)}"` : ''}
                  >${esc(action.label)}</button>`).join('')}
            </span>` : ''}
          </span>
        </div>
      </article>`;
  };

  /* Reglages de quantite : une seule ecoute pour tout le projet. La page qui
     affiche les vignettes n'a qu'a se rafraichir sur l'evenement emis. */
  document.addEventListener('click', async (event) => {
    const button = event.target.closest('[data-mm]');
    if (!button) return;
    event.preventDefault();
    event.stopPropagation();

    const [scope, direction] = button.dataset.mm.split('-');
    const name = button.dataset.card;
    const delta = direction === 'inc' ? 1 : -1;
    const value = button.parentElement.querySelector('.stepper__value');
    button.disabled = true;

    try {
      if (scope === 'coll') {
        const res = await MM.api.post('/api/v2/collection/adjust', {
          card_name: name, delta,
          // La ligne visee, quand la vignette en designe une : une carte peut
          // exister en plusieurs editions dans la collection.
          id: button.dataset.item ? Number(button.dataset.item) : undefined,
        });
        if (value) value.textContent = MM.fmt.int(res.quantity);
      } else {
        const deck = MM.deckContext;
        if (!deck) throw new Error('Aucun deck sélectionné.');
        await MM.api.post(delta > 0 ? '/api/deck-card/add' : '/api/deck-card/remove',
          { deck_id: deck.deck_id, commander: deck.commander, card_name: name });
        if (value) value.textContent = MM.fmt.int(Math.max(0, Number(value.textContent) + delta));
      }
      document.dispatchEvent(new CustomEvent('mm:card-change', {
        detail: { card_name: name, scope, delta,
                  id: button.dataset.item ? Number(button.dataset.item) : null },
      }));
    } catch (error) {
      MM.toast.error(error.message);
    } finally {
      button.disabled = false;
    }
  }, true);

  /** Squelettes de chargement pour une grille de cartes. */
  /**
   * Fenetre de detail d'une carte : son texte, ce qu'on en possede, et toutes
   * ses editions cotees. Ouverte depuis le titre d'une carte, sur n'importe
   * quel ecran — d'ou le simple nom en entree.
   *
   * @param {string} name
   */
  MM.cardDetail = async function (name, options = {}) {
    const dialog = MM.modal({
      title: name, wide: true,
      body: '<p class="muted">Chargement…</p>',
    });

    let data;
    try {
      data = await MM.api.get(`/api/v2/cards/${encodeURIComponent(name)}/detail`);
    } catch (error) {
      dialog.body.innerHTML = MM.empty({
        icon: 'warning', title: 'Carte introuvable', text: error.message });
      return;
    }

    const card = data.card;
    const printings = data.printings || [];
    // L'edition la moins chere sert de reference de prix dans tout le projet :
    // elle ouvre la fiche et porte un repere dans la liste.
    const priced = printings.filter((p) => p.low_price != null);
    const cheapest = priced.length
      ? priced.reduce((best, p) => (p.low_price < best.low_price ? p : best))
      : null;

    // Ligne de collection a laquelle rattacher l'edition choisie : celle d'ou
    // la fiche a ete ouverte, sinon la seule que l'on possede.
    const items = data.items || [];
    const target = items.find((line) => line.id === options.itemId)
      || (items.length === 1 ? items[0] : null);
    const current = target && target.scryfall_id;

    // L'edition retenue pour cette carte, possedee ou non : c'est elle que
    // montrent les autres ecrans, donc elle qui ouvre la fiche.
    const preferred = data.preferred_printing || null;
    const chosen = printings.find((p) => p.scryfall_id === (current || preferred));
    const shown = chosen || cheapest || printings[0] || {};

    const stats = [];
    if (card.power != null) stats.push(`${esc(card.power)}/${esc(card.toughness)}`);
    if (card.loyalty != null) stats.push(`Loyauté ${esc(card.loyalty)}`);
    if (card.defense != null) stats.push(`Défense ${esc(card.defense)}`);

    const tags = [];
    if (card.legal_commander) tags.push('<span class="badge">Peut être commandant</span>');
    if (card.game_changer) tags.push('<span class="badge badge--warn">Game changer</span>');
    if (card.popularity_rank) {
      tags.push(`<span class="badge badge--info"
        title="Rang de popularité dans les decks publics, du plus joué au moins joué"
        >Popularité nº ${MM.fmt.int(card.popularity_rank)}</span>`);
    }

    dialog.body.innerHTML = `
      <div class="card-detail">
        <div class="card-detail__art">
          <img src="${esc(MM.img.card(shown) || '')}" alt="${esc(card.name)}">
          <p class="xs dim">${shown.set_name
            ? `${esc(shown.set_name)} · ${esc(shown.set_code || '')}` : ''}</p>
        </div>

        <div class="card-detail__info">
          <div class="card-detail__head">
            <p class="h3">${esc(card.name)}</p>
            <span class="grow"></span>
            ${MM.mana.cost(card.mana_cost)}
          </div>
          <p class="small dim">${MM.img.pips(card.color_identity)} ${esc(card.type_line)}
            ${stats.length ? `· <span class="strong">${stats.join(' · ')}</span>` : ''}</p>

          ${card.oracle_text
            ? `<p class="card-detail__oracle">${esc(card.oracle_text).replace(/\n/g, '<br>')}</p>`
            : ''}

          ${tags.length ? `<div class="card-detail__tags">${tags.join('')}</div>` : ''}

          <div class="card-detail__mine">
            <span>${data.owned
              ? `<span class="strong">${MM.fmt.plural(data.owned, 'exemplaire')}</span> en collection`
              : '<span class="dim">Absente de votre collection</span>'}</span>
            ${data.decks.length ? `<span class="dim">·</span>
              <span>Jouée dans ${data.decks.map((d) =>
                `<a href="/decks/${encodeURIComponent(d.deck_id)}">${esc(d.name)}</a>`)
                .join(', ')}</span>` : ''}
          </div>

          <div class="card-detail__market">
            <a class="btn btn--sm" target="_blank" rel="noopener"
               href="${esc(MM.market.buy(card.name))}">Acheter</a>
            <a class="btn btn--sm" target="_blank" rel="noopener"
               href="${esc(MM.market.sell(card.name))}">Vendre</a>
          </div>
        </div>
      </div>

      <div class="card-detail__editions">
        <p class="label">${MM.fmt.plural(printings.length, 'édition')}
          ${priced.length < printings.length
            ? `<span class="dim">· ${MM.fmt.int(printings.length - priced.length)} sans cote</span>`
            : ''}</p>
        <div class="card-grid card-grid--lg editions">
          ${printings.map((p) => `
            <article class="mtg-card ${p === cheapest ? 'is-cheapest' : ''}
                            ${p.scryfall_id === (current || preferred) ? 'is-mine' : ''}"
                     data-pick="${esc(p.scryfall_id)}"
                     title="${target
                       ? 'Enregistrer cette édition comme celle que vous possédez'
                       : 'Montrer cette carte dans cette édition partout'}">
              <div class="mtg-card__frame">
                ${MM.img.frame({
                  card_name: card.name,
                  image_normal: p.image_normal, image_small: p.image_small,
                })}
              </div>
              <div class="mtg-card__foot">
                <span class="mtg-card__name" data-no-detail
                      title="${esc(p.set_name || p.set_code || '')}"
                  >${esc(p.set_name || p.set_code || '—')}</span>
                <span class="mtg-card__meta">
                  <span>${esc(p.set_code || '')}${p.collector_number
                    ? ` #${esc(p.collector_number)}` : ''}</span>
                  <span class="dim">${esc(MM.fmt.rarity(p.rarity))}</span>
                  ${p.low_price != null
                    ? `<span class="mtg-card__price">${MM.fmt.eur(p.low_price)}</span>`
                    : '<span class="dim">non cotée</span>'}
                </span>
                <span class="mtg-card__meta">
                  <span class="dim">${MM.fmt.date(p.released_at)}</span>
                  ${p.foil_low != null
                    ? `<span class="dim">foil ${MM.fmt.eur(p.foil_low)}</span>` : ''}
                  ${p.scryfall_id === current
                    ? '<span class="badge badge--ok">votre édition</span>'
                    : (p.scryfall_id === preferred
                      ? '<span class="badge badge--ok">édition retenue</span>'
                      : (p === cheapest
                        ? '<span class="badge badge--info" title="Prix de référence du projet">moins chère</span>'
                        : ''))}
                </span>
              </div>
            </article>`).join('')}
        </div>
      </div>`;

    // Choisir une edition la retient pour cette carte : c'est elle que les
    // ecrans montreront desormais, qu'on la possede ou non. Quand la fiche
    // vise une ligne de collection, le meme geste dit aussi quelle edition on
    // a dans ses boites.
    dialog.body.addEventListener('click', async (event) => {
      const picked = event.target.closest('[data-pick]');
      if (!picked) return;
      const printing = picked.dataset.pick;
      try {
        await MM.api.post(
          `/api/v2/cards/${encodeURIComponent(card.name)}/printing`,
          { scryfall_id: printing });
        if (target) {
          await MM.api.post(`/api/v2/collection/items/${target.id}/printing`,
            { scryfall_id: printing });
        }
        els('.mtg-card.is-mine', dialog.body).forEach((node) => {
          node.classList.remove('is-mine');
          const flag = el('.badge--ok', node);
          if (flag) flag.remove();
        });
        picked.classList.add('is-mine');
        // Le repere se pose sur la derniere ligne d'informations, celle qui
        // porte deja la date et la mention « moins chere ».
        const lines = els('.mtg-card__meta', picked);
        const foot = lines[lines.length - 1];
        if (foot) {
          const stale = el('.badge--info', foot);
          if (stale) stale.remove();
          foot.insertAdjacentHTML('beforeend',
            `<span class="badge badge--ok">${target ? 'votre édition' : 'édition retenue'}</span>`);
        }
        MM.toast.ok('Édition enregistrée.');
        document.dispatchEvent(new CustomEvent('mm:card-change', {
          detail: { card_name: card.name, scope: 'printing', delta: 0,
                    id: target ? target.id : null },
        }));
      } catch (error) {
        MM.toast.error(error.message);
      }
    });

  };

  // Le titre d'une carte ouvre sa fiche, sur tous les ecrans. En phase de
  // capture : la vignette entiere est souvent cliquable pour autre chose.
  document.addEventListener('click', (event) => {
    const label = event.target.closest(
      '[data-card-detail], .mtg-card__name, .mtg-card__frame, .deck-line__name');
    if (!label || label.hasAttribute('data-no-detail')) return;
    // Un ecran peut envoyer ailleurs le clic sur l'illustration ; le nom, lui,
    // ouvre toujours la fiche.
    const detour = label.dataset.href;
    if (detour) {
      event.preventDefault();
      event.stopPropagation();
      location.href = detour;
      return;
    }

    const tile = label.closest('.mtg-card');
    const itemId = tile && tile.dataset.id ? Number(tile.dataset.id) : null;
    const name = label.dataset.cardDetail
      || (label.classList.contains('mtg-card__frame')
        ? (tile && tile.dataset.name)
        : (label.getAttribute('title') || label.textContent.trim()));
    if (!name) return;
    event.preventDefault();
    event.stopPropagation();
    MM.cardDetail(name, { itemId });
  }, true);

  MM.cardSkeletons = function (count = 12) {
    return Array.from({ length: count }, () => `
      <div class="mtg-card">
        <div class="skeleton skeleton--card"></div>
        <div class="skeleton skeleton--text" style="width:70%;margin-top:8px"></div>
      </div>`).join('');
  };

  /**
   * Fenetre « Ajouter une carte au deck » : un nom a chercher, les resultats
   * en grille, et le reglage « dans le deck » de chaque vignette pour ajouter.
   *
   * La recherche part sur Entree, pas a chaque frappe : les resultats
   * s'affichent assez grands pour reconnaitre une carte a son illustration
   * avant de l'ajouter. La fenetre ne se ferme pas apres un ajout — on enchaine
   * les cartes — et rend la main au champ, son texte selectionne.
   *
   * Le deck vise est celui de MM.deckContext ; l'ecran qui l'ouvre se met a
   * jour en ecoutant « mm:card-change ».
   *
   * @param {object} options
   *   title   {string} titre de la fenetre
   *   deckQty {(name:string)=>number} exemplaires deja dans le deck, pour que
   *     le reglage d'une carte deja presente ne reparte pas d'un zero trompeur
   * @returns {object} la fenetre { root, body, close }
   */
  MM.addCardDialog = function ({ title = 'Ajouter une carte au deck',
                                 deckQty = () => 0 } = {}) {
    const dialog = MM.modal({
      title,
      wide: true,
      body: `<div class="stack">
               <div class="field">
                 <label class="label" for="mmAddCard">Nom de la carte</label>
                 <input class="input" id="mmAddCard" type="text" autocomplete="off"
                        placeholder="Tapez un nom puis appuyez sur Entrée">
               </div>
               <div id="mmAddResults"></div>
             </div>`,
      onClose: () => document.removeEventListener('mm:card-change', onChange),
    });

    const input = el('#mmAddCard', dialog.root);
    const host = el('#mmAddResults', dialog.root);

    async function search() {
      const term = input.value.trim();
      if (term.length < 2) {
        host.innerHTML = '<p class="muted small">Saisissez au moins deux caractères.</p>';
        return;
      }
      host.innerHTML = `<div class="card-grid">${MM.cardSkeletons(8)}</div>`;
      try {
        const data = await MM.api.get(
          `/api/v2/cards/suggest?q=${encodeURIComponent(term)}&limit=20`);
        const found = data.cards || [];
        if (!found.length) {
          host.innerHTML = MM.empty({
            icon: 'search', title: 'Aucune carte trouvée',
            text: `Aucune carte ne correspond à « ${term} ».` });
          return;
        }
        host.innerHTML = `<div class="card-grid">${found.map((card) => MM.cardTile(card, {
          context: 'deck',
          deckQty: deckQty(card.card_name || card.name || ''),
          note: card.printed_name && card.printed_name !== card.name
            ? esc(card.printed_name) : '',
        })).join('')}</div>`;
      } catch (err) {
        host.innerHTML = MM.empty({
          icon: 'warning', title: 'Recherche impossible', text: err.message });
      }
    }

    // Apres un ajout, le champ reprend la main, son texte selectionne : le nom
    // suivant se tape sans avoir a effacer le precedent.
    function onChange(event) {
      if (event.detail.scope !== 'deck') return;
      input.focus();
      input.select();
    }
    document.addEventListener('mm:card-change', onChange);

    input.addEventListener('keydown', (event) => {
      if (event.key !== 'Enter') return;
      event.preventDefault();
      search();
    });

    input.focus();
    return dialog;
  };

  /**
   * Resout un lot de noms de cartes (illustration, rarete, prix, possession).
   * Les endpoints d'analyse ne renvoient que des noms.
   *
   * @param {string[]} names
   * @returns {Promise<Object<string, object>>} indexe par le nom demande
   */
  /* ══ Mana d'un deck ════════════════════════════════════════════════════ */

  /** Barres de la courbe : les couts 0 a 6, puis 7 et plus. La reference
   *  servie par l'API suit le meme decoupage. */
  const BUCKETS = 8;

  /** Une reference par commandant et par page : elle ne bouge pas d'un
   *  affichage a l'autre, et plusieurs courbes peuvent la partager. */
  const referenceCache = new Map();

  MM.mana = {
    COLORS: ['W', 'U', 'B', 'R', 'G'],

    /** Un cout « {2}{U}{U} » rendu en jetons lisibles. */
    cost(value) {
      const parts = (value || '').match(/\{[^}]+\}/g) || [];
      if (!parts.length) return '';
      return `<span class="mana-cost">${parts.map((symbol) => {
        const inner = symbol.slice(1, -1);
        const color = MM.mana.COLORS.find((c) => inner === c);
        return `<span class="mana-token ${color ? `mana-token--${color}` : ''}"
                      title="${esc(symbol)}">${esc(inner)}</span>`;
      }).join('')}</span>`;
    },

    /** Symboles colores exiges par les couts. Un symbole hybride compte pour
     *  chacune de ses couleurs, faute de savoir laquelle sera payee. */
    symbols(list) {
      const counts = { W: 0, U: 0, B: 0, R: 0, G: 0 };
      list.forEach((card) => {
        const matches = (card.mana_cost || '').match(/\{[^}]+\}/g) || [];
        matches.forEach((symbol) => {
          MM.mana.COLORS.forEach((color) => {
            if (symbol.includes(color)) counts[color] += card.quantity || 1;
          });
        });
      });
      return counts;
    },

    /** Sources de mana : toute carte dont le texte ajoute du mana d'une
     *  couleur — terrains comme artefacts. « any » compte a part. */
    sources(list) {
      const counts = { W: 0, U: 0, B: 0, R: 0, G: 0, any: 0 };
      list.forEach((card) => {
        const found = new Set();
        // « Add » jusqu'a la fin de la phrase : evite de compter un symbole
        // mentionne ailleurs dans le texte de la carte.
        ((card.oracle_text || '').match(/[Aa]dd[^.]*/g) || []).forEach((segment) => {
          if (/any color/i.test(segment)) found.add('any');
          MM.mana.COLORS.forEach((color) => {
            if (segment.includes(`{${color}}`)) found.add(color);
          });
        });
        found.forEach((key) => { counts[key] += card.quantity || 1; });
      });
      return counts;
    },

    /** Terrains du deck : ceux qui en sont un d'emblee, et ceux qui ne le
     *  deviennent qu'au verso — a ne pas confondre dans un compte de mana. */
    lands(list) {
      let front = 0;
      let back = 0;
      list.forEach((card) => {
        const faces = (card.type_line || '').split('//');
        const quantity = card.quantity || 1;
        if (/\bLand\b/i.test(faces[0])) front += quantity;
        else if (faces.length > 1 && /\bLand\b/i.test(faces.slice(1).join(' '))) {
          back += quantity;
        }
      });
      return { front, back };
    },

    /** Sorts du deck : ni terrains, ni commandant. Le commandant est une
     *  donnee du deck et non un choix de construction ; le compter fausserait
     *  la comparaison avec des decks qui le portent tous. Une carte dont le
     *  cout est inconnu sort du calcul plutot que de passer pour un sort a
     *  zero mana. */
    spells(list, commander = null) {
      return (list || []).filter((card) => {
        if (/Land/i.test(card.type_line || '')) return false;
        if (card.mana_value === null || card.mana_value === undefined) return false;
        return !(commander && MM.isCommander(commander, card.card_name));
      });
    },

    /** Cout de mana moyen des sorts, chaque exemplaire comptant pour un.
     *  null quand le deck n'a aucun sort au cout connu. */
    average(list, commander = null) {
      let sum = 0;
      let copies = 0;
      MM.mana.spells(list, commander).forEach((card) => {
        const quantity = card.quantity || 1;
        sum += Number(card.mana_value) * quantity;
        copies += quantity;
      });
      return copies ? sum / copies : null;
    },

    /** Part de chaque cout dans les sorts du deck, en pourcentage. */
    shares(list, commander = null) {
      const buckets = new Array(BUCKETS).fill(0);
      MM.mana.spells(list, commander).forEach((card) => {
        buckets[Math.min(BUCKETS - 1, Math.floor(card.mana_value))] += card.quantity || 1;
      });
      const total = buckets.reduce((sum, count) => sum + count, 0);
      return { buckets, total, shares: buckets.map((c) => (total ? (c / total) * 100 : 0)) };
    },

    /** Courbe de mana, terrains et commandant exclus. Chaque barre porte sa
     *  part du deck, et la plus haute occupe toute la hauteur donnee.
     *
     *  `commander` et `reference` sont facultatifs : donnes, ils ajoutent la
     *  courbe moyenne des decks qui jouent le meme commandant, en repere par
     *  dessus les barres, et la comparaison des couts moyens sous la courbe.
     *  Sans reference, le cout moyen du deck s'affiche seul. */
    curve(list, height = 40, { commander = null, reference = null } = {}) {
      const { buckets, total, shares } = MM.mana.shares(list, commander);
      const refShares = (reference && reference.curve && reference.curve.length === BUCKETS)
        ? reference.curve : null;
      // Une echelle commune aux deux courbes, sans quoi le repere se lirait
      // dans une autre unite que la barre qu'il surplombe.
      const scale = Math.max(...shares, ...(refShares || []), 1);

      const bars = shares.map((share, index) => {
        const label = index === BUCKETS - 1 ? `${index}+` : `${index}`;
        const count = buckets[index];
        const refShare = refShares ? refShares[index] : null;
        const title = `${count} carte${count > 1 ? 's' : ''} à ${label}`
          + ` — ${Math.round(share)} % des sorts`
          + (refShare === null ? '' : `, contre ${Math.round(refShare)} % pour ce commandant`);
        return `
        <span class="curve__bar" title="${esc(title)}">
          <span class="curve__pct">${count ? `${Math.round(share)} %` : ''}</span>
          <span class="curve__track">
            <span class="curve__fill" style="height:${(share / scale) * 100}%"></span>
            ${refShare === null ? '' : `<span class="curve__ref"
              style="bottom:${(refShare / scale) * 100}%"></span>`}
          </span>
          <span class="curve__label">${label}</span>
        </span>`;
      }).join('');

      return `<div class="curve-block">
        <div class="curve" style="--curve-h:${height}px">${bars}</div>
        ${total ? MM.mana.averageLine(list, commander, reference) : ''}
      </div>`;
    },

    /** Le cout moyen du deck, compare a celui des decks du meme commandant. */
    averageLine(list, commander = null, reference = null) {
      const average = MM.mana.average(list, commander);
      if (average === null) return '';

      const ref = reference && reference.avg_mana_value ? reference : null;
      const delta = ref ? average - ref.avg_mana_value : null;
      // Un deck plus cher n'est ni meilleur ni pire : l'ecart se donne sans
      // jugement, d'ou l'absence de couleur.
      const gap = delta === null ? ''
        : `<span class="curve-avg__delta">(${delta >= 0 ? '+' : '−'}${MM.fmt.dec(Math.abs(delta))})</span>`;
      const title = ref
        ? `Coût de mana moyen des sorts du deck, hors terrains et commandant, `
          + `comparé à ${MM.fmt.dec(ref.avg_mana_value)} pour ${MM.fmt.int(ref.decks)} `
          + `deck${ref.decks > 1 ? 's' : ''} jouant ${ref.commander}`
        : 'Coût de mana moyen des sorts du deck, hors terrains et commandant';

      return `<div class="curve-avg" title="${esc(title)}">
        <span class="curve-avg__label">Coût moyen</span>
        <b class="curve-avg__value">${MM.fmt.dec(average)}</b>
        ${ref ? `<span class="curve-avg__ref">
          <i class="curve-avg__key" aria-hidden="true"></i>
          vs <b>${MM.fmt.dec(ref.avg_mana_value)}</b>
          <span class="dim">des decks ${esc(ref.commander)}</span></span> ${gap}` : ''}
      </div>`;
    },

    /** Courbe moyenne des decks publics jouant ce commandant, ou null.
     *  Les pages l'attendent avant de dessiner leur courbe ; une meme page en
     *  demandant plusieurs fois ne declenche qu'un appel. */
    reference(commander) {
      const name = String(commander || '').trim();
      if (!name) return Promise.resolve(null);
      if (!referenceCache.has(name)) {
        referenceCache.set(name, MM.api
          .get('/api/v2/stats/mana-curve' + MM.api.qs({ commander: name }))
          .then((data) => (data && data.reference) || null)
          // La comparaison est un supplement : son echec laisse la courbe.
          .catch(() => null));
      }
      return referenceCache.get(name);
    },

    /** Une ligne de comptes colores, avec la part de chacun. */
    row(label, counts, extra) {
      const shown = MM.mana.COLORS.filter((color) => counts[color] > 0);
      if (!shown.length && !extra) return '';
      // Les sources « de n'importe quelle couleur » restent hors du calcul :
      // elles alimentent toutes les couleurs et fausseraient la repartition.
      const total = shown.reduce((sum, color) => sum + counts[color], 0);
      return `
        <div class="mana-row">
          <span class="mana-row__label">${esc(label)}</span>
          ${shown.map((color) => `
            <span class="mana-count"
                  title="${counts[color]} sur ${total} — ${esc(label.toLowerCase())} ${color}">
              <span class="pip pip--${color}"></span>${counts[color]}
              <span class="dim">${Math.round((counts[color] / total) * 100)} %</span></span>`).join('')}
          ${extra || ''}
        </div>`;
    },

    /** Symboles exiges, sources et terrains — la lecture des couleurs. */
    rows(list) {
      const src = MM.mana.sources(list);
      const land = MM.mana.lands(list);
      return `
        <div class="mana-rows">
          ${MM.mana.row('Symboles', MM.mana.symbols(list))}
          ${MM.mana.row('Sources', src, src.any
            ? `<span class="xs dim">+ ${src.any} toutes couleurs</span>` : '')}
          <div class="mana-row">
            <span class="mana-row__label">Terrains</span>
            <span class="mana-count"><span class="strong">${land.front}</span></span>
            ${land.back ? `<span class="xs dim"
              title="Cartes dont seule la face arrière est un terrain"
              >+ ${land.back} au verso</span>` : ''}
          </div>
        </div>`;
    },

    /** Courbe et repartition cote a cote, pour les ecrans qui n'ont qu'une
     *  place a leur donner. */
    block(list, height = 40, options = {}) {
      return `<div class="mana-block">
        ${MM.mana.curve(list, height, options)}
        ${MM.mana.rows(list)}
      </div>`;
    },
  };

  MM.resolveCards = async function (names) {
    const unique = [...new Set(names.filter(Boolean))];
    if (!unique.length) return {};
    const chunks = [];
    for (let i = 0; i < unique.length; i += 400) chunks.push(unique.slice(i, i + 400));
    const results = await Promise.all(
      chunks.map((chunk) => MM.api.post('/api/v2/cards/resolve', { names: chunk })));
    return Object.assign({}, ...results.map((result) => result.cards || {}));
  };

  /**
   * Selecteur de deck partage par les pages d'analyse.
   * Preselectionne ?deck= de l'URL, sinon le premier deck.
   *
   * @param {HTMLElement} host  conteneur ou injecter le champ
   * @param {(deck:object)=>void} onChange
   * @returns {Promise<object[]>} les decks charges
   */
  /**
   * Selecteur de deck.
   * @param {object} options { label, autoSelect } — autoSelect:false laisse le
   *   champ vide et n'analyse rien tant que l'utilisateur n'a pas choisi. Un
   *   deck passe dans l'URL reste honore dans les deux cas.
   */
  MM.deckPicker = async function (host, onChange,
                                  { label = 'Deck à analyser', autoSelect = true } = {}) {
    host.innerHTML = '<div class="skeleton skeleton--text" style="width:260px;height:38px"></div>';
    let decks = [];
    let lastDeckId = null;
    try {
      const data = await MM.api.get('/api/v2/decks');
      decks = data.decks;
      // Le deck sur lequel on travaille, retenu d'une visite a l'autre.
      lastDeckId = data.last_deck_id || null;
    } catch (err) {
      host.innerHTML = `<p class="small" style="color:var(--danger)">${esc(err.message)}</p>`;
      return [];
    }

    if (!decks.length) {
      host.innerHTML = MM.empty({
        icon: 'decks',
        title: 'Aucun deck à analyser',
        text: 'Importez une decklist pour utiliser cet outil.',
        action: { href: '/collection/import', label: 'Importer un deck' },
      });
      return [];
    }

    // Le deck vise par l'URL prime ; sinon on reprend celui de la derniere
    // fois, sur cet ecran comme sur les autres. Sans souvenir, le premier deck
    // de la liste, quand l'ecran en demande un d'emblee.
    const requested = new URLSearchParams(location.search).get('deck');
    const current = decks.find((deck) => deck.deck_id === requested)
      || decks.find((deck) => deck.deck_id === lastDeckId)
      || (autoSelect ? decks[0] : null);

    // Le menu se lit de A a Z : c'est par son nom qu'on y cherche un deck.
    // L'ordre de l'API, du plus recemment modifie au plus ancien, reste celui
    // du choix par defaut ci-dessus.
    const ordered = decks.slice()
      .sort((a, b) => (a.name || '').localeCompare(b.name || '', 'fr'));

    host.innerHTML = `
      <div class="field" style="max-width:420px">
        <label class="label" for="mmDeckPick">${esc(label)}</label>
        <select class="select" id="mmDeckPick">
          ${current ? '' : '<option value="" selected>Choisissez un deck…</option>'}
          ${ordered.map((deck) => `
            <option value="${esc(deck.deck_id)}" ${deck === current ? 'selected' : ''}>
              ${esc(deck.name)}
            </option>`).join('')}
        </select>
      </div>`;

    /** Retenir le deck courant : un echec ici ne doit rien interrompre. */
    function remember(deckId) {
      if (deckId === lastDeckId) return;
      lastDeckId = deckId;
      MM.api.post('/api/v2/decks/last', { deck_id: deckId })
        .catch(() => { /* le choix vaut pour cette page, faute de mieux */ });
    }

    el('#mmDeckPick', host).addEventListener('change', (event) => {
      const deck = decks.find((entry) => entry.deck_id === event.target.value);
      if (!deck) return;
      const url = new URL(location.href);
      url.searchParams.set('deck', deck.deck_id);
      history.replaceState(null, '', url);
      remember(deck.deck_id);
      onChange(deck);
    });

    // La bande de decks de l'ecran de depart selectionne le deck sans passer
    // par le menu deroulant : les deux doivent rester d'accord.
    host._mmSelect = (deckId) => {
      const select = el('#mmDeckPick', host);
      if (!select) return false;
      select.value = deckId;
      select.dispatchEvent(new Event('change'));
      return true;
    };

    if (current) {
      remember(current.deck_id);
      onChange(current);
    }
    return decks;
  };

  /** Selectionne un deck dans le selecteur monte sur `host`. */
  MM.pickDeck = function (host, deckId) {
    return Boolean(host && host._mmSelect && host._mmSelect(deckId));
  };

  /**
   * Les commandants d'un deck. Ils peuvent etre deux — Partner, Background,
   * Doctor's companion — et tiennent alors dans une seule chaine, « A & B »,
   * les noms ranges par ordre alphabetique. C'est sous cette forme que les
   * statistiques indexent les decks : miroir de manamind/commanders.py.
   *
   * « // » ne separe pas deux commandants mais les deux faces d'une meme
   * carte, qui n'en fait qu'un.
   *
   * @param {string} raw  le champ commander du deck
   * @returns {string[]}
   */
  MM.commanders = function (raw) {
    return String(raw || '').split(/\s*[&+]\s*/)
      .map((name) => name.trim())
      .filter((name) => name && name.toLowerCase() !== 'unknown');
  };

  /** Cette carte est-elle l'un des commandants du deck ?
   *
   *  Un commandant recto-verso s'ecrit « A // B » ou « A » selon le
   *  deckbuilder d'origine : les deux ecritures designent la meme carte, et
   *  n'en reconnaitre qu'une la laissait passer pour une carte ordinaire du
   *  deck — comptee dans la courbe, envoyee a l'analyse, proposee au retrait.
   */
  MM.isCommander = function (raw, cardName) {
    const front = (name) => name.split(' // ')[0].trim();
    const target = String(cardName || '').trim().toLowerCase();
    if (!target) return false;
    const keys = new Set();
    MM.commanders(raw).forEach((name) => {
      const key = name.toLowerCase();
      keys.add(key);
      keys.add(front(key));
    });
    return keys.has(target) || keys.has(front(target));
  };

  /**
   * Un deck dont l'import n'a pas identifie le commandant est enregistre sous
   * « Unknown ». Les analyses partent toutes du commandant : sans lui, elles
   * ne renvoient rien d'exploitable.
   */
  MM.commanderMissing = function (deck) {
    const name = ((deck && deck.commander) || '').trim().toLowerCase();
    return !name || name === 'unknown';
  };

  /** Invite a designer un commandant, sur la fiche du deck concerne. */
  MM.commanderPrompt = function (deck) {
    return MM.empty({
      icon: 'crown',
      title: 'Ce deck n’a pas de commandant',
      text: `L'import n'a pas su identifier le commandant de « ${esc(deck.name)} ». `
        + 'Désignez-le depuis la fiche du deck : la couronne, à côté du nom de chaque '
        + 'carte légendaire, en fait le commandant.',
      action: { href: `/decks/${encodeURIComponent(deck.deck_id)}`,
                label: 'Ouvrir la fiche du deck' },
    });
  };

  MM.empty = function ({ icon = 'box', title, text, action }) {
    return `
      <div class="empty">
        <span class="empty__mark">${MM.icons[icon] || MM.icons.box}</span>
        <div class="stack-2" style="align-items:center">
          <p class="empty__title">${esc(title)}</p>
          <p class="empty__text">${esc(text || '')}</p>
        </div>
        ${action ? `<a class="btn btn--primary" href="${esc(action.href)}">
                      ${esc(action.label)}</a>` : ''}
      </div>`;
  };

  /**
   * Ecran de depart d'un outil.
   *
   * Les pages d'analyse s'ouvraient sur un titre, un menu deroulant et un vide :
   * rien ne disait ce que l'outil allait chercher ni sur quoi il s'appuie. Ce
   * bloc occupe l'attente et repond aux deux questions avant le premier clic.
   *
   * @param {object} options
   *   icon        cle dans MM.icons, pour la pastille de tete
   *   title, text l'intention de l'outil, en une phrase
   *   steps       [{ title, text }] — les trois temps du parcours
   *   checks      [{ strong, text }] — ce que l'outil prend en compte
   *   checksTitle intitule de la colonne de droite
   *   note        la limite a connaitre, en bas de la colonne de droite
   */
  MM.toolIntro = function ({ icon = 'sparkle', title, text = '', steps = [],
                             checks = [], checksTitle = "Ce que l'outil regarde",
                             note = '' } = {}) {
    const walk = steps.map((step, index) => `
      <div class="walk__step">
        <span class="walk__index">${index + 1}</span>
        <div class="stack-2" style="gap:2px">
          <p class="walk__title">${esc(step.title)}</p>
          <p class="walk__text">${esc(step.text)}</p>
        </div>
      </div>`).join('');

    const list = checks.map((item) => `
      <div class="checks__item">
        ${MM.icons.check}
        <span>${item.strong ? `<b>${esc(item.strong)}</b> ` : ''}${esc(item.text)}</span>
      </div>`).join('');

    return `
      <div class="intro">
        <div class="intro__panel">
          <div class="intro__head">
            <span class="intro__mark">${MM.icons[icon] || MM.icons.sparkle}</span>
            <div class="stack-2" style="gap:4px">
              <p class="intro__title">${esc(title)}</p>
              ${text ? `<p class="small muted">${esc(text)}</p>` : ''}
            </div>
          </div>
          ${walk ? `<div class="walk">${walk}</div>` : ''}
        </div>

        ${(list || note) ? `
          <div class="intro__panel intro__panel--soft">
            ${list ? `<div class="stack-2" style="gap:var(--sp-4)">
                        <p class="h4">${esc(checksTitle)}</p>
                        <div class="checks">${list}</div>
                      </div>` : ''}
            ${note ? `<p class="note"><b>Bon à savoir.</b> ${esc(note)}</p>` : ''}
          </div>` : ''}
      </div>`;
  };

  /**
   * Bande de decks cliquables. Elle double le menu deroulant : l'illustration
   * du commandant vaut mieux qu'un nom dans une liste pour reconnaitre un deck.
   */
  MM.deckStrip = function (decks, { limit = 8, title = 'Démarrer sur un de vos decks',
                                    text = '' } = {}) {
    const shown = decks.slice(0, limit);
    if (!shown.length) return '';

    const chips = shown.map((deck) => {
      const art = deck.scryfall_id ? MM.scryfallArt(deck.scryfall_id) : null;
      const percent = Math.round((deck.owned_ratio || 0) * 100);
      return `
        <button class="deck-chip" type="button" data-deck="${esc(deck.deck_id)}">
          ${art ? `<span class="deck-chip__art"
                         style="background-image:url('${esc(art)}')"></span>` : ''}
          <span class="deck-chip__name truncate">${esc(deck.name)}</span>
          <span class="deck-chip__meta truncate">
            ${MM.fmt.int(deck.card_count || 0)} cartes · ${percent} % en collection
          </span>
        </button>`;
    }).join('');

    return `
      <section class="section">
        <div class="section-head">
          <div class="section-head__title">
            <h3 class="h3">${esc(title)}</h3>
            ${decks.length > shown.length
              ? `<span class="muted small">${MM.fmt.int(decks.length)} au total</span>`
              : ''}
          </div>
          <a class="btn btn--sm" href="/decks">Tous mes decks</a>
        </div>
        ${text ? `<p class="muted small" style="margin-top:-8px">${esc(text)}</p>` : ''}
        <div class="deck-strip">${chips}</div>
      </section>`;
  };

  /** Branche les vignettes de MM.deckStrip sur un selecteur de deck. */
  MM.bindDeckStrip = function (root, onPick) {
    root.addEventListener('click', (event) => {
      const chip = event.target.closest('[data-deck]');
      if (chip && root.contains(chip)) onPick(chip.dataset.deck);
    });
  };

  /**
   * Legende de lecture posee au-dessus d'une grille : elle nomme les reperes
   * que porte chaque vignette, qui restaient sinon a deviner.
   */
  MM.legend = function (items) {
    return `
      <div class="legend">
        ${items.map((item) => `
          <span class="legend__item">${MM.icons[item.icon] || MM.icons.check}
            <span>${item.strong ? `<b>${esc(item.strong)}</b> ` : ''}${esc(item.text)}</span>
          </span>`).join('')}
      </div>`;
  };

  global.MM = MM;
})(window);
