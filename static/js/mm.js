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

    eur(value, { compact = false } = {}) {
      if (value === null || value === undefined) return '—';
      const amount = Number(value);
      if (compact && amount >= 1000) {
        return nfEur.format(Math.round(amount)).replace(/,00/, '');
      }
      return nfEur.format(amount);
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
        { key: 'import', label: 'Importer une liste',
          href: '/collection/import', icon: 'upload' },
        { key: 'boosters', label: "Sélectionner les extensions que j'ai ouvertes",
          href: '/collection/boosters', icon: 'booster' },
      ],
    },
    {
      label: 'Analyser',
      items: [
        { key: 'build', label: 'Decks à construire', href: '/collection/commandants', icon: 'crown' },
        { key: 'improve', label: 'Améliorer mon deck avec les cartes de ma collection',
          href: '/decks/ameliorer', icon: 'sparkle' },
        { key: 'swap', label: 'Trouver un nouveau commandant pour mon deck',
          href: '/decks/commandant', icon: 'swap' },
        { key: 'moves', label: 'Cartes à changer de deck', href: '/decks/deplacements', icon: 'move' },
        { key: 'analyze', label: 'Améliorer mon deck grâce à des analyses IA',
          href: '/decks/analyse', icon: 'chart' },
        { key: 'card', label: 'Trouver un commandant pour une carte',
          href: '/cartes/commandant', icon: 'search' },
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
        <button class="nav__item" id="mmLogout" style="width:100%">
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

  /**
   * Vignette de carte de collection.
   * @param {object} item exemplaire renvoye par l'API
   * @param {object} options { actions:boolean, muted:boolean, price:boolean }
   */
  MM.cardTile = function (item, options = {}) {
    const { actions = false, muted = false, price = true, qty = true,
            market = false } = options;
    const finish = MM.fmt.finish(item.finish);
    // La finition et le nombre d'exemplaires descendent sous la carte, avec
    // l'edition et le prix : poses sur l'illustration, ils masquaient le titre
    // et l'art de la carte.
    const badges = [];
    if (item.in_decks && item.in_decks.length) {
      badges.push(`<span class="badge badge--info" title="${esc(item.in_decks.join(', '))}">
        ${item.in_decks.length} deck${item.in_decks.length > 1 ? 's' : ''}</span>`);
    }

    return `
      <article class="mtg-card ${muted ? 'mtg-card--muted' : ''}" data-id="${esc(item.id ?? '')}"
               data-name="${esc(item.card_name || '')}">
        <div class="mtg-card__frame">
          ${MM.img.frame(item)}
          ${badges.length ? `<div class="mtg-card__badges">${badges.join('')}</div>` : ''}
          ${actions ? `
            <div class="mtg-card__actions">
              <span class="stepper">
                <button data-act="dec" aria-label="Retirer un exemplaire">−</button>
                <span class="stepper__value">${item.quantity ?? 0}</span>
                <button data-act="inc" aria-label="Ajouter un exemplaire">+</button>
              </span>
              <button class="btn btn--sm btn--icon" data-act="detail" style="margin-left:auto"
                      aria-label="Détails">${MM.icons.chevron}</button>
            </div>` : ''}
        </div>
        <div class="mtg-card__foot">
          <span class="mtg-card__name" title="${esc(item.card_name || '')}">
            ${esc(item.card_name || '')}
          </span>
          <span class="mtg-card__meta">
            ${item.set_code ? `<span>${esc(item.set_code)}</span>` : ''}
            ${qty && item.quantity > 1 ? `<span title="Exemplaires en collection"
              >×${item.quantity}</span>` : ''}
            ${finish ? `<span class="accent">${esc(finish)}</span>` : ''}
            ${price && item.unit_price != null
              ? `<span class="mtg-card__price">${MM.fmt.eur(item.unit_price)}</span>` : ''}
          </span>
          ${market ? `
            <span class="mtg-card__market">
              <a class="btn btn--sm" target="_blank" rel="noopener"
                 href="${esc(MM.market.sell(item.card_name))}"
                 title="Proposer ${esc(item.card_name || '')} à la vente sur RELIC-TRADE"
                >Vendre</a>
              <a class="btn btn--sm" target="_blank" rel="noopener"
                 href="${esc(MM.market.buy(item.card_name))}"
                 title="Voir les offres d'achat de ${esc(item.card_name || '')} sur Cardmarket"
                >Acheter</a>
            </span>` : ''}
        </div>
      </article>`;
  };

  /** Squelettes de chargement pour une grille de cartes. */
  MM.cardSkeletons = function (count = 12) {
    return Array.from({ length: count }, () => `
      <div class="mtg-card">
        <div class="skeleton skeleton--card"></div>
        <div class="skeleton skeleton--text" style="width:70%;margin-top:8px"></div>
      </div>`).join('');
  };

  /**
   * Resout un lot de noms de cartes (illustration, rarete, prix, possession).
   * Les endpoints d'analyse ne renvoient que des noms.
   *
   * @param {string[]} names
   * @returns {Promise<Object<string, object>>} indexe par le nom demande
   */
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
    try {
      const data = await MM.api.get('/api/v2/decks');
      decks = data.decks;
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

    const requested = new URLSearchParams(location.search).get('deck');
    const current = decks.find((deck) => deck.deck_id === requested)
      || (autoSelect ? decks[0] : null);

    host.innerHTML = `
      <div class="field" style="max-width:420px">
        <label class="label" for="mmDeckPick">${esc(label)}</label>
        <select class="select" id="mmDeckPick">
          ${current ? '' : '<option value="" selected>Choisissez un deck…</option>'}
          ${decks.map((deck) => `
            <option value="${esc(deck.deck_id)}" ${deck === current ? 'selected' : ''}>
              ${esc(deck.name)}
            </option>`).join('')}
        </select>
      </div>`;

    el('#mmDeckPick', host).addEventListener('change', (event) => {
      const deck = decks.find((entry) => entry.deck_id === event.target.value);
      if (!deck) return;
      const url = new URL(location.href);
      url.searchParams.set('deck', deck.deck_id);
      history.replaceState(null, '', url);
      onChange(deck);
    });

    if (current) onChange(current);
    return decks;
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

  global.MM = MM;
})(window);
