#!/usr/bin/env python3
"""Mercado Livre onboarding isolated from LIVE. Secrets are entered locally, never passed in argv."""
import argparse
import getpass
import json
from pathlib import Path
import sys
import webbrowser

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'apps/api/src'))
from aicommerce.providers.ml_onboarding import OnboardingError, ReadOnlyOnboarding, callback_server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['init', 'start', 'finish', 'refresh', 'check', 'status', 'serve'])
    parser.add_argument('--vault', type=Path, default=Path.home() / '.local/share/aicommerce/ml-readonly')
    parser.add_argument('--authorized', action='store_true', help='Owner has consented to the requested OAuth/GET action')
    parser.add_argument('--open', action='store_true', help='Open authorization in the local browser (start only)')
    parser.add_argument('--accept-platform-user-write', action='store_true',
                        help='init only: aceitar o escopo write que o DevCenter impõe à permissão padrão Usuários '
                             '(cliente continua só GET; demais permissões devem estar em leitura/sem acesso)')
    parser.add_argument('--port', type=int, default=8094, help='Loopback callback port (serve only); no public bind option')
    args = parser.parse_args()
    vault = args.vault.absolute()
    if any((p / '.git').exists() for p in [vault, *vault.parents]):
        parser.error('Keep the private vault outside Git repositories')
    if args.action in ('start', 'finish', 'refresh', 'check', 'serve') and not args.authorized:
        parser.error('Prior owner authorization is required; never send credentials in chat')
    try:
        flow = ReadOnlyOnboarding(vault)
        if args.action == 'init':
            result = flow.configure(input('ID da aplicação Mercado Livre: ').strip(),
                                    getpass.getpass('Client secret (entrada privada): '),
                                    input('Redirect URI HTTPS fixo cadastrado: ').strip(),
                                    accept_platform_user_write=args.accept_platform_user_write)
        elif args.action == 'start':
            if not args.open:
                parser.error('Use start --authorized --open to open the official authorization page privately')
            url = flow.start()
            opened = webbrowser.open(url)
            del url
            result = {'state': 'AWAITING_OWNER_CONSENT', 'browser_open_requested': opened,
                      'note': 'Conclua pessoalmente no navegador. Código/state expiram em 10 minutos.'}
        elif args.action == 'finish':
            result = flow.finish(getpass.getpass('URL de retorno completa (entrada privada): '), authorized=True)
        elif args.action == 'refresh':
            result = flow.refresh(authorized=True)
        elif args.action == 'check':
            result = flow.check(authorized=True)
        elif args.action == 'serve':
            server = callback_server(flow, args.port, authorized=True)
            print('Callback somente em loopback. HTTPS público e proxy controlado ainda precisam ser configurados.')
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()
            return 0
        else:
            result = flow.status()
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result['state'] not in ('ACCOUNT_MISMATCH', 'REAUTH_REQUIRED', 'PERMISSION_DENIED',
                                           'UNAVAILABLE', 'INVALID_RESPONSE', 'RATE_LIMITED',
                                           'DEFERRED_RATE_LIMIT', 'REJECTED') else 1
    except OnboardingError as exc:
        out = {'state': str(exc), 'commercial_capabilities': False}
        try:  # código OAuth padronizado do último erro de troca (sem mensagem nem corpo remoto)
            st = ReadOnlyOnboarding(vault).status()
            out['last_exchange_error'] = st.get('last_exchange_error')
            out['last_token_rejection'] = st.get('last_token_rejection')
        except Exception:
            pass
        print(json.dumps(out))
        return 1
    except Exception:
        # Never expose callback URL, token body, ciphertext, private paths or a traceback to logs.
        print(json.dumps({'state': 'PRIVATE_CONFIGURATION_OR_CALLBACK_INVALID', 'commercial_capabilities': False}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
