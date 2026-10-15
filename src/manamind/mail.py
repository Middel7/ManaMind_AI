"""mail.py — Envoi d'e-mails transactionnels par SMTP.

Un seul usage aujourd'hui : le lien de réinitialisation du mot de passe. Le
module ne dépend d'aucun fournisseur : Brevo, Resend, OVH ou un compte Gmail
exposent tous un serveur SMTP, et il suffit de renseigner les variables
SMTP_* dans le .env du serveur.

Sans SMTP_HOST, l'envoi est simplement indisponible : `mail_configured()` le
dit, et l'interface oriente alors vers l'administrateur au lieu de promettre
un e-mail qui ne partira jamais.
"""
from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

logger = logging.getLogger(__name__)

# Délai de connexion au serveur SMTP : au-delà, la requête HTTP qui attend
# l'envoi resterait suspendue, et l'utilisateur avec elle.
_SMTP_TIMEOUT_S = 15


def mail_configured() -> bool:
    """Vrai si un serveur SMTP et une adresse d'expédition sont renseignés."""
    return bool(os.environ.get("SMTP_HOST") and os.environ.get("MAIL_FROM"))


def send_mail(to: str, subject: str, body: str) -> bool:
    """Envoie un e-mail en texte brut. Retourne False si l'envoi a échoué.

    Le port 465 ouvre une connexion chiffrée d'emblée ; tout autre port
    (587 par défaut) passe en STARTTLS. Une erreur est journalisée et non
    levée : l'appelant répond la même chose à l'utilisateur dans tous les cas,
    pour ne pas révéler si une adresse possède un compte.
    """
    if not mail_configured():
        logger.warning("Envoi d'e-mail demandé alors que SMTP_HOST ou MAIL_FROM manque")
        return False

    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER", "")
    password = os.environ.get("SMTP_PASSWORD", "")

    message = EmailMessage()
    message["From"] = os.environ["MAIL_FROM"]
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)

    context = ssl.create_default_context()
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=_SMTP_TIMEOUT_S, context=context) as smtp:
                if user:
                    smtp.login(user, password)
                smtp.send_message(message)
        else:
            with smtplib.SMTP(host, port, timeout=_SMTP_TIMEOUT_S) as smtp:
                smtp.starttls(context=context)
                if user:
                    smtp.login(user, password)
                smtp.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        logger.error("Échec de l'envoi d'un e-mail à %s : %s", to, exc)
        return False
    return True
