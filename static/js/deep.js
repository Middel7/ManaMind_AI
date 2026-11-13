/* deep.js — Analyse poussee d'un deck, partagee par deux ecrans : l'onglet
   « Analyse poussee » des resultats d'analyse, et la page /analyse/poussee.

   Rend, dans le conteneur donne :
   - le mana du deck (courbe, symboles, sources, terrains, cout moyen), face
     aux decks du meme commandant ;
   - les categories les plus jouees par ces decks, puis l'interaction, chaque
     ligne ouvrant les cartes de sa categorie.

   Depend de mm.js, charge avant lui. */
(function () {
  'use strict';

  // Part des decks similaires qui jouent la carte, au format court des
  // resultats : la note partage sa ligne avec le prix. Sous 1 %, un arrondi a
  // zero laisserait croire que personne ne la joue.
  const rateNote = (rate) => {
    if (!rate) return 'aucun deck';
    if (rate < 1) return '< 1 % des decks';
    return `${MM.fmt.int(rate)} % des decks`;
  };

  // Les cartes du deck sont deja la : ni achat ni vente. Les absentes se
  // proposent a l'achat. La recherche de commandant n'a rien a faire ici.
  const tile = (card, resolved, market) => MM.cardTile(
    { ...(resolved[card.card_name] || {}), card_name: card.card_name },
    { context: null, note: rateNote(card.rate), market, commander: false });

  const tiles = (cards, resolved, market) => `
    <div class="card-grid">
      ${cards.map((card) => tile(card, resolved, market)).join('')}
    </div>`;

  const EMPTY_DECK = '<p class="muted small">Aucune carte de votre deck dans cette catégorie.</p>';
  const EMPTY_MISSING = '<p class="muted small">Votre deck contient déjà toutes les cartes'
    + ' de cette catégorie jouées ailleurs.</p>';

  const headDeck = (row) => `
    <h3 class="h4">Dans votre deck
      <span class="badge">${MM.fmt.int(row.cards.length)}</span></h3>
    <p class="xs dim">De la moins jouée à la plus jouée par les decks du même
      commandant.</p>`;
  const headMissing = () => `
    <h3 class="h4">Jouées ailleurs, absentes de votre deck</h3>
    <p class="xs dim">Les plus jouées par les decks du même commandant, que votre
      deck ne contient pas.</p>`;

  /** Grand ecran : une grille de six cartes par rangee, trois du deck a
   *  gauche, trois absentes a droite, placees rangee par rangee. */
  function sideBySide(row, resolved) {
    const place = (cards, firstColumn, market) => cards.map((card, i) => `
      <div style="grid-column:${firstColumn + (i % 3)};grid-row:${2 + Math.floor(i / 3)}">
        ${tile(card, resolved, market)}</div>`).join('');
    return `
      <div class="deep-modal__grid">
        <div class="stack-2 deep-modal__deck" style="grid-row:1">${headDeck(row)}</div>
        <div class="stack-2 deep-modal__missing" style="grid-row:1">${headMissing()}</div>
        ${row.cards.length ? place(row.cards, 1, false)
          : `<div class="deep-modal__deck" style="grid-row:2">${EMPTY_DECK}</div>`}
        ${row.missing.length ? place(row.missing, 5, true)
          : `<div class="deep-modal__missing" style="grid-row:2">${EMPTY_MISSING}</div>`}
      </div>`;
  }

  /** Ecran etroit : les deux listes l'une sous l'autre. */
  function stacked(row, resolved) {
    return `
      <div class="stack-6">
        <section class="stack-2">${headDeck(row)}
          ${row.cards.length ? tiles(row.cards, resolved, false) : EMPTY_DECK}</section>
        <section class="stack-2">${headMissing()}
          ${row.missing.length ? tiles(row.missing, resolved, true) : EMPTY_MISSING}</section>
      </div>`;
  }

  const narrow = window.matchMedia('(max-width: 1080px)');

  async function openCategory(row) {
    // La fenetre peut se fermer avant la fin du chargement des cartes.
    let closed = false;
    let stopRender = null;
    const dialog = MM.modal({
      title: row.category,
      wide: 'xl',
      body: `<div class="card-grid">${MM.cardSkeletons(6)}</div>`,
      onClose: () => { closed = true; if (stopRender) stopRender(); },
    });
    // Elargie au-dela de la fiche carte : voir .deep-modal dans app.css.
    dialog.root.querySelector('.modal__box').classList.add('deep-modal');

    let resolved = {};
    try {
      resolved = await MM.resolveCards(
        [...row.cards, ...row.missing].map((card) => card.card_name));
    } catch { /* les vignettes s'affichent sans image plutot que pas du tout */ }

    // La disposition suit la largeur, y compris si la fenetre du navigateur
    // change de taille pendant que la fenetre est ouverte.
    const render = () => {
      dialog.body.innerHTML = narrow.matches
        ? stacked(row, resolved) : sideBySide(row, resolved);
    };
    render();
    if (!closed) {
      narrow.addEventListener('change', render);
      stopRender = () => narrow.removeEventListener('change', render);
    }
  }

  /** Le panneau des categories ; `rows` vient de l'API des categories. */
  function categoriesPanel(rows) {
    // Une seule echelle pour toutes les lignes : une barre se compare aussi a
    // celles des autres categories, pas seulement a sa voisine.
    const max = Math.max(1, ...rows.flatMap((row) => [row.deck, row.similar]));
    const ranked = rows.filter((row) => !row.always).length;

    // L'index de la ligne dans `rows` sert au clic : il est garde tel quel,
    // meme une fois les deux groupes separes.
    const rowHtml = (row, index) => {
      const below = row.deck < row.similar;
      return `
        <button class="gap-row deep-row" type="button" data-row="${index}"
                aria-label="Voir les cartes de la catégorie ${MM.esc(row.category)}">
          <span class="gap-row__label truncate" title="${MM.esc(row.category)}">
            ${MM.esc(row.category)}</span>
          <span class="gap-row__track"
                title="Votre deck : ${MM.fmt.int(row.deck)} — decks similaires : ${MM.fmt.dec(row.similar, 1)} en moyenne">
            <span class="gap-row__deck" style="width:${(row.deck / max) * 100}%"></span>
            <span class="gap-row__tick" style="left:${(row.similar / max) * 100}%"></span>
          </span>
          <span class="gap-row__delta">
            <b class="${below ? 'accent' : ''}">${MM.fmt.int(row.deck)}</b>
            <span class="dim">/ ${MM.fmt.dec(row.similar, 1)}</span></span>
        </button>`;
    };

    return `
      <div class="panel deep-panel">
        <div class="panel__head">
          <div class="stack-2">
            <h2 class="h3">Les ${ranked} catégories les plus jouées</h2>
            <p class="xs dim">Classées selon les decks du même commandant. Pour chacune,
              le nombre de cartes de votre deck, puis le nombre moyen de cartes par deck
              chez les autres. Cliquez sur une catégorie pour en voir les cartes.</p>
          </div>
        </div>
        <div class="panel__body">
          ${rows.map((row, index) => (row.always ? '' : rowHtml(row, index))).join('')}
          <div class="stack-2 deep-panel__always">
            <h3 class="h4">Interaction</h3>
            <p class="xs dim">Toujours affichée, quel que soit son rang : la capacité
              du deck à répondre à ce que jouent les adversaires.</p>
          </div>
          ${rows.map((row, index) => (row.always ? rowHtml(row, index) : '')).join('')}
          <div class="legend" style="margin-top:var(--sp-4)">
            <span class="legend__item"><b>Barre dorée</b> votre deck</span>
            <span class="legend__item"><b>Trait blanc</b> moyenne des decks similaires</span>
            <span class="legend__item"><b class="accent">Chiffre doré</b> moins que la moyenne</span>
          </div>
          <p class="xs dim" style="margin-top:var(--sp-3)">
            Une carte compte dans chaque catégorie qu’elle touche : les totaux dépassent
            donc le nombre de cartes du deck. Le commandant et les terrains de base ne
            sont pas comptés.
          </p>
        </div>
      </div>`;
  }

  /**
   * Rend l'analyse poussee d'un deck dans `host`.
   *
   * @param {HTMLElement} host
   * @param {object} options
   *   deckId    {string}  le deck analyse
   *   commander {string}  pour la reference de mana ; a defaut, celui de l'API
   *   deckCards {Array}   cartes du deck, si l'ecran les a deja ; sinon lues
   *   before    {string}  HTML pose avant le mana (le score du moteur IA…)
   *   onData    {(data)=>void} recoit la reponse de l'API des categories
   * @returns {Promise<{setDeckCards:(cards:Array)=>void}>} pour redessiner le
   *   mana quand le deck change
   */
  MM.deepAnalysis = async function (host, {
    deckId, commander = null, deckCards = null, before = '', onData = null,
  } = {}) {
    host.innerHTML = `
      <div class="stack-6">
        ${before}
        <div id="deepMana"></div>
        <div id="deepCategories">
          <div class="center" style="padding:var(--sp-8)"><span class="spinner spinner--lg"></span></div>
        </div>
      </div>`;

    let cards = deckCards;
    let reference = null;
    let cmd = commander;
    let landsAvg = null;     // terrains moyens des decks similaires (API)
    let curveRows = null;    // cartes de chaque cout, une fois l'API revenue

    // La courbe de reference vient d'une API, la moyenne de terrains d'une
    // autre : les deux se fondent en une seule reference pour mm.js.
    const mergedReference = () => {
      if (!reference && landsAvg == null) return null;
      return { ...(reference || {}), lands: landsAvg };
    };

    // La derniere barre reunit les couts de 7 et plus.
    const costLabel = (bucket) => (bucket === 7 ? '7 mana et plus' : `${bucket} mana`);

    function renderMana() {
      const target = MM.el('#deepMana', host);
      if (!target || !cards || !cards.length) return;
      const ref = mergedReference();
      // Une courbe plus haute que dans le bandeau d'ou elle vient : c'est sa
      // hauteur qui porte la comparaison aux decks du meme commandant.
      target.innerHTML = `
        <div class="panel">
          <div class="panel__body deep-mana">
            <div class="stack-2">
              <h2 class="h3">Mana du deck</h2>
              <p class="xs dim">Courbe de mana, symboles, sources, terrains et coût moyen,
                comparés aux decks du même commandant.</p>
              <p class="xs dim">Cliquez sur une barre de la courbe pour voir les cartes
                de ce coût.</p>
            </div>
            ${MM.mana.curve(cards, 170, { commander: cmd, reference: ref, average: false })}
            ${MM.mana.rows(cards, { commander: cmd, reference: ref, average: true })}
          </div>
        </div>`;
      // Chaque barre ouvre les cartes de son cout : un bouton pour le clavier
      // et les lecteurs d'ecran, sans changer le dessin de mm.js.
      MM.els('.curve__bar[data-bucket]', target).forEach((bar) => {
        const label = costLabel(Number(bar.dataset.bucket));
        bar.setAttribute('role', 'button');
        bar.tabIndex = 0;
        bar.setAttribute('aria-label', `Voir les cartes à ${label}`);
      });
    }

    function openBucket(bucket) {
      if (!curveRows || !curveRows[bucket]) return;
      openCategory({
        category: `Cartes à ${costLabel(bucket)}`,
        ...curveRows[bucket],
      });
    }

    const manaHost = MM.el('#deepMana', host);
    manaHost.addEventListener('click', (event) => {
      const bar = event.target.closest('.curve__bar[data-bucket]');
      if (bar) openBucket(Number(bar.dataset.bucket));
    });
    manaHost.addEventListener('keydown', (event) => {
      if (event.key !== 'Enter' && event.key !== ' ') return;
      const bar = event.target.closest('.curve__bar[data-bucket]');
      if (!bar) return;
      event.preventDefault();
      openBucket(Number(bar.dataset.bucket));
    });

    const loadReference = () => {
      if (!cmd) return;
      MM.mana.reference(cmd).then((ref) => {
        if (!ref) return;
        reference = ref;
        renderMana();
      }).catch(() => {});
    };

    if (cards) renderMana();
    else {
      MM.api.get(`/api/v2/decks/${encodeURIComponent(deckId)}`)
        .then((detail) => { cards = detail.cards || []; renderMana(); })
        .catch(() => { /* le panneau de mana manque, les categories restent */ });
    }
    if (cmd) loadReference();

    const categories = MM.el('#deepCategories', host);
    let data;
    try {
      data = await MM.api.get(`/api/v2/decks/${encodeURIComponent(deckId)}/categories`);
    } catch (err) {
      categories.innerHTML = MM.empty({ icon: 'warning', title: 'Catégories indisponibles', text: err.message });
      return { setDeckCards() {} };
    }
    if (onData) onData(data);
    curveRows = data.curve || null;
    landsAvg = data.lands_avg == null ? null : data.lands_avg;
    if (!cmd && data.deck && data.deck.commander) {
      cmd = data.deck.commander;
      loadReference();
    }
    renderMana();

    const rows = data.categories || [];
    categories.innerHTML = rows.length ? categoriesPanel(rows) : MM.empty({
      icon: 'box',
      title: 'Aucun deck similaire connu',
      text: 'Aucun deck public de ce commandant n’est encore en base : '
        + 'il n’y a rien à quoi comparer ce deck.',
    });
    categories.addEventListener('click', (event) => {
      const button = event.target.closest('[data-row]');
      if (button) openCategory(rows[Number(button.dataset.row)]);
    });

    return {
      setDeckCards(next) { cards = next; renderMana(); },
    };
  };
})();
