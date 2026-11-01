"""Comandos administrativos.

Uso:
  python -m aicommerce.manage create-user --email x@y --name "Nome" --role owner
  python -m aicommerce.manage reset-mfa --email x@y
  python -m aicommerce.manage deactivate-user --email x@y

A senha é gerada aleatoriamente (ou lida de --password-stdin) e exibida uma única vez. O MFA NÃO é gerado
aqui: papéis que exigem segundo fator fazem o cadastro com QR code no primeiro login, e o segredo nunca
passa por terminal/chat. `reset-mfa` remove o fator e força um novo cadastro no próximo login.
"""

from __future__ import annotations

import argparse
import getpass
import secrets
import sys

from sqlalchemy import select

from aicommerce.db import session_scope
from aicommerce.governance import audit
from aicommerce.identity import mfa, rbac
from aicommerce.identity.security import hash_password
from aicommerce.identity.service import create_user
from aicommerce.models import User, UserRole

MIN_PASSWORD = 12


class CommandError(Exception):
    pass


def _create_user(email: str, name: str, roles: list[str], password: str | None) -> dict:
    unknown = [r for r in roles if r not in rbac.ROLES]
    if unknown:
        raise CommandError(f"papel desconhecido: {', '.join(unknown)} (válidos: {', '.join(rbac.ROLES)})")
    generated = password is None
    password = password or secrets.token_urlsafe(18)
    if len(password) < MIN_PASSWORD:
        raise CommandError(f"senha precisa de ao menos {MIN_PASSWORD} caracteres")
    needs_mfa = any(rbac.ROLES[r][1] for r in roles)
    with session_scope() as db:
        if db.execute(select(User).where(User.email == email.lower().strip())).scalar_one_or_none():
            raise CommandError(f"já existe usuário com e-mail {email}")
        user = create_user(db, email=email, display_name=name, password=password, roles=roles)
        audit.record(
            db,
            actor_type="system",
            actor_id="manage.create-user",
            action="user.created",
            subject_type="user",
            subject_id=str(user.id),
            payload={"roles": roles, "mfa": needs_mfa},
        )
        user_id = str(user.id)
    return {
        "user_id": user_id,
        "email": email.lower().strip(),
        "roles": roles,
        "password": password if generated else None,
        "mfa_enrollment_on_first_login": needs_mfa,
    }


def _reset_mfa(email: str) -> None:
    with session_scope() as db:
        user = db.execute(select(User).where(User.email == email.lower().strip())).scalar_one_or_none()
        if user is None:
            raise CommandError(f"usuário {email} não encontrado")
        mfa.reset(db, user, actor="manage.reset-mfa")


def _set_password(email: str, password: str) -> None:
    """Redefine a senha (esqueceu a atual). Encerra todas as sessões e desliga os computadores lembrados."""
    if len(password) < MIN_PASSWORD:
        raise CommandError(f"senha precisa de ao menos {MIN_PASSWORD} caracteres")
    if email.split("@")[0].lower() in password.lower():
        raise CommandError("a senha não pode conter o e-mail")
    with session_scope() as db:
        user = db.execute(
            select(User).where(User.email == email.lower().strip()).with_for_update()
        ).scalar_one_or_none()
        if user is None:
            raise CommandError(f"usuário {email} não encontrado")
        user.password_hash = hash_password(password)
        user.session_version += 1  # derruba sessões e credenciais de computadores lembrados
        user.failed_logins, user.failed_mfa_attempts, user.locked_until = 0, 0, None
        audit.record(
            db,
            actor_type="system",
            actor_id="manage.set-password",
            action="user.password.reset",
            subject_type="user",
            subject_id=str(user.id),
        )


def _deactivate(email: str) -> None:
    """Desativa a conta e derruba sessões (ex.: usuário de teste E2E, acesso de ex-colaborador sem outro owner).
    O último proprietário ativo nunca é desativado por aqui."""
    with session_scope() as db:
        user = db.execute(
            select(User).where(User.email == email.lower().strip()).with_for_update()
        ).scalar_one_or_none()
        if user is None:
            raise CommandError(f"usuário {email} não encontrado")
        roles = set(db.execute(select(UserRole.role_name).where(UserRole.user_id == user.id)).scalars())
        if "owner" in roles and user.is_active:
            others = db.execute(
                select(User.id)
                .join(UserRole, UserRole.user_id == User.id)
                .where(UserRole.role_name == "owner", User.is_active.is_(True), User.id != user.id)
            ).first()
            if others is None:
                raise CommandError("não é possível desativar o último proprietário ativo")
        user.is_active = False
        user.session_version += 1
        audit.record(
            db,
            actor_type="system",
            actor_id="manage.deactivate-user",
            action="user.deactivated",
            subject_type="user",
            subject_id=str(user.id),
        )


def rotate_pii_key() -> dict[str, int]:
    """Regrava com a chave ATUAL (AIC_PII_KEY) tudo que foi cifrado com as anteriores (AIC_PII_PREVIOUS_KEYS):
    dados de clientes (e o pseudônimo, que também depende da chave), tokens de canal, PKCE em andamento e segredos
    de MFA (os legados em claro passam a ser cifrados). Uma transação; idempotente — rodar de novo não muda nada."""
    from aicommerce.identity import pii
    from aicommerce.models import ChannelAccount, OAuthState, Order

    out = {"orders": 0, "channel_tokens": 0, "oauth_states": 0, "mfa_secrets": 0}

    def fresh(token: str | None) -> str | None:
        if token is None or pii.uses_current_key(token):
            return None
        return pii.encrypt(pii.decrypt(token))

    with session_scope() as db:
        for o in db.execute(select(Order).where(Order.customer_ciphertext.is_not(None)).with_for_update()).scalars():
            new = fresh(o.customer_ciphertext)
            if new:
                customer = pii.decrypt(new)
                o.customer_ciphertext = new
                o.customer_ref = pii.pseudonym(str(customer.get("email") or customer.get("name") or o.external_id))
                out["orders"] += 1
        for acc in db.execute(select(ChannelAccount).with_for_update()).scalars():
            for attr in ("access_token_ciphertext", "refresh_token_ciphertext"):
                new = fresh(getattr(acc, attr))
                if new:
                    setattr(acc, attr, new)
                    out["channel_tokens"] += 1
        for st in db.execute(select(OAuthState).with_for_update()).scalars():
            new = fresh(st.code_verifier_ciphertext)
            if new:
                st.code_verifier_ciphertext = new
                out["oauth_states"] += 1
        for user in db.execute(select(User).with_for_update()).scalars():
            for attr in ("totp_secret", "totp_pending_secret"):
                stored = getattr(user, attr)
                if stored is None:
                    continue
                sealed = stored.startswith(pii.SEAL_PREFIX)
                if sealed and pii.uses_current_key(stored.removeprefix(pii.SEAL_PREFIX)):
                    continue
                setattr(user, attr, pii.seal(pii.unseal(stored)))
                out["mfa_secrets"] += 1
        audit.record(
            db,
            actor_type="system",
            actor_id="manage.rotate-pii-key",
            action="security.pii_key.rotated",
            subject_type="system",
            subject_id="pii",
            payload=out,
        )
    return out


def _ask_password() -> str:
    first = getpass.getpass("Nova senha (mín. 12 caracteres; não aparece ao digitar): ")
    if first != getpass.getpass("Repita a nova senha: "):
        raise CommandError("as senhas não conferem")
    return first


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="aicommerce.manage")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create-user")
    c.add_argument("--email", required=True)
    c.add_argument("--name", required=True)
    c.add_argument("--role", action="append", required=True, dest="roles")
    c.add_argument("--password-stdin", action="store_true", help="lê a senha da entrada padrão")
    r = sub.add_parser("reset-mfa")
    r.add_argument("--email", required=True)
    sp = sub.add_parser("set-password", help="redefine a senha de quem esqueceu a atual (pede a nova sem eco)")
    sp.add_argument("--email", required=True)
    sp.add_argument("--password-stdin", action="store_true", help="lê a senha da entrada padrão (automação)")
    d = sub.add_parser("deactivate-user", help="desativa a conta e encerra as sessões")
    d.add_argument("--email", required=True)
    pr = sub.add_parser("publish-desktop-release", help="publica uma versão do app desktop (atualização automática)")
    pr.add_argument("--version", required=True)
    pr.add_argument("--notes", default="")
    pr.add_argument(
        "--artifact",
        action="append",
        required=True,
        metavar="PLATAFORMA=ARQUIVO",
        help="ex.: darwin-aarch64=/tmp/AI.Commerce.app.tar.gz (o .sig ao lado do arquivo)",
    )
    rv = sub.add_parser("revoke-desktop-release", help="tira uma versão do ar (apps deixam de recebê-la)")
    rv.add_argument("--version", required=True)
    sub.add_parser("reindex-kb", help="regrava os vetores da base de conhecimento com o embedder configurado")
    sub.add_parser(
        "rotate-pii-key",
        help="regrava dados cifrados com a chave nova (AIC_PII_KEY) lendo as antigas de AIC_PII_PREVIOUS_KEYS",
    )
    a = p.parse_args(argv)
    try:
        if a.cmd == "create-user":
            pwd = sys.stdin.readline().rstrip("\n") if a.password_stdin else None
            info = _create_user(a.email, a.name, a.roles, pwd)
            print(f"Usuário criado: {info['email']} papéis={info['roles']} id={info['user_id']}")
            if info["password"]:
                print(f"  Senha (exibida uma única vez): {info['password']}")
            if info["mfa_enrollment_on_first_login"]:
                print("  MFA: cadastro com QR code no primeiro login.")
        elif a.cmd == "set-password":
            pwd = sys.stdin.readline().rstrip("\n") if a.password_stdin else _ask_password()
            _set_password(a.email, pwd)
            print(f"Senha de {a.email} redefinida. Sessões encerradas; entre de novo no app com a senha nova.")
        elif a.cmd == "publish-desktop-release":
            import pathlib

            from aicommerce import desktop_updates as du

            arts = {}
            for item in a.artifact:
                plat, sep, path = item.partition("=")
                if not sep:
                    raise CommandError(f"use PLATAFORMA=ARQUIVO (recebido {item!r})")
                arts[plat.strip()] = pathlib.Path(path).expanduser()
            try:
                with session_scope() as db:
                    out = du.publish(db, version=a.version, notes=a.notes, artifacts=arts, actor="manage")
                    lines = [
                        f"  {x.platform}: {x.filename} ({x.size_bytes // 1024} KB) sha256={x.sha256[:12]}" for x in out
                    ]
            except du.ReleaseError as e:
                raise CommandError(str(e)) from e
            print(f"Versão {a.version} publicada:")
            print("\n".join(lines))
        elif a.cmd == "revoke-desktop-release":
            from aicommerce import desktop_updates as du

            try:
                with session_scope() as db:
                    du.revoke(db, a.version, actor="manage")
            except du.ReleaseError as e:
                raise CommandError(str(e)) from e
            print(f"Versão {a.version} revogada: apps deixam de recebê-la.")
        elif a.cmd == "reindex-kb":
            from aicommerce.ai import knowledge

            try:
                with session_scope() as db:
                    r = knowledge.reindex(db)
            except knowledge.KnowledgeError as e:
                raise CommandError(str(e)) from e
            print(f"Base de conhecimento: {r['reindexed']} trechos regravados com {r['embedder']}.")
            print(f"Faltam {r['remaining']}.")
        elif a.cmd == "rotate-pii-key":
            n = rotate_pii_key()
            print(f"Regravado com a chave atual: {n}. Confira o app e então remova AIC_PII_PREVIOUS_KEYS.")
        elif a.cmd == "deactivate-user":
            _deactivate(a.email)
            print(f"Usuário {a.email} desativado; sessões encerradas.")
        else:
            _reset_mfa(a.email)
            print(f"MFA removido para {a.email}: novo cadastro com QR code no próximo login.")
    except CommandError as e:
        print(f"erro: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
