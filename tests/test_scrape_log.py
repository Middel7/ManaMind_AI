"""Journal des tâches de scrape : la page doit retrouver ce qui est nouveau."""
from __future__ import annotations

from manamind.routers.scrape import _append_log, _new_job


def test_le_compteur_ne_redescend_pas_quand_le_journal_est_coupe():
    job = _new_job()
    for i in range(1001):
        _append_log(job, f"ligne {i}")

    assert len(job["log"]) == 801
    assert job["log_total"] == 1001


def test_la_page_retrouve_toutes_les_lignes_malgre_les_coupes():
    """Rejoue le calcul de `pollJob` (admin.html) : 5 000 lignes, lues par paquets."""
    job = _new_job()
    seen = 0
    shown = []
    for i in range(5000):
        _append_log(job, str(i))
        if i % 5 == 4:
            logs, total = job["log"], job["log_total"]
            shown += [e["msg"] for e in logs[max(0, len(logs) - (total - seen)):]]
            seen = total

    assert shown == [str(i) for i in range(5000)]
