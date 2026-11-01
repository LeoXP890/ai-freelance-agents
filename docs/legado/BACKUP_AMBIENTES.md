# Backups — separação de staging e produção

## Proteções implementadas

O script `scripts/backup.py` identifica a **stack selecionada** no `AIC_ENV_FILE`
(`AIC_STACK` e `POSTGRES_DB`), respeitando variáveis definidas explicitamente
pelo serviço systemd. Os arquivos cifrados passam a ficar em **pastas diferentes**:

- `backups/dryrun/` — simulação local e CI;
- `backups/staging/` — staging na VPS, quando houver backup explícito;
- `backups/live/` — produção, com autorização e gates separados.

Cada pasta tem sua própria passphrase de fallback, lista de arquivos e retenção.
O manifesto identifica `stack` e `database`. O teste de restauração **recusa** de
forma antecipada o manifesto de outro ambiente ou com hash de arquivo divergente,
antes de executar qualquer comando de criação/restauração do banco. O nome do banco
temporário também inclui a stack para não colidir entre ambientes.

Dumps temporários sem criptografia são sempre apagados mesmo que o processo de
cifragem falhe. Isso diminui o risco de vazamento de dados pessoais no disco.

Os backups remotos também são enviados a um subdiretório `/<stack>` do caminho
configurado em `AIC_BACKUP_REMOTE`, evitando colisões entre arquivos homônimos.

## Como executar (somente ambiente já autorizado)

Produção via `systemd` permanece configurada como
`AIC_ENV_FILE=.../live.env`, `AIC_COMPOSE_PRODUCTION=1`. Em CLI manual, exportar
`AIC_ENV_FILE` com a pasta apropriada e garantir a passphrase independente.
Nunca executar `restore-test` de uma stack apontando para outra: a verificação
do manifesto impede essa operação.

**Atenção à mudança de layout:** arquivos criados por versões antigas do script
continuam no diretório histórico `backups/` e não são movidos nem apagados
automaticamente. Antes de usar a nova versão em uma VPS com backups existentes,
compare os manifestos e migre os arquivos manualmente para a pasta da stack
correspondente, com a respectiva passphrase. Se não souber a origem ou chave,
mantenha os arquivos arquivados e não tente restaurá-los em ambiente de produção.

## Evidência necessária antes da operação real

- CI verde do PR da proteção de backups;
- no servidor verdadeiro, serviço de backup automático concluído sem erro;
- restauração real de teste na mesma stack com relatório `ok=true`;
- arquivo cifrado verificado também no destino externo;
- confirmação de que a passphrase da produção está protegida fora da VPS.

**Testes em CI não constituem prova de backup da VPS nem liberação de vendas.**
