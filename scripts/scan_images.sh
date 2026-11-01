#!/usr/bin/env bash
# Scan das imagens do stack (INF-007): vulnerabilidades HIGH/CRITICAL com correção disponível e segredos.
# Falha se houver vulnerabilidade corrigível ou segredo. Relatório: progress/test-reports/image_scan.json
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TRIVY="aquasec/trivy:0.75.0"
WORK="$(mktemp -d)"
trap 'rm -rf "${WORK:?}"' EXIT
cp "$ROOT/infra/security/trivy-secret.yaml" "$WORK/"
for img in aicommerce-dryrun-api aicommerce-dryrun-web; do
  docker save "${img}:latest" -o "$WORK/${img}.tar"
  docker run --rm -v "$WORK:/scan" -v aic-trivy-cache:/root/.cache "$TRIVY" image --quiet \
    --scanners vuln,secret --secret-config /scan/trivy-secret.yaml --severity HIGH,CRITICAL \
    --format json --input "/scan/${img}.tar" > "$WORK/${img}.json"
  rm -f "$WORK/${img}.tar"
done
python3 - "$WORK" "$ROOT/progress/test-reports/image_scan.json" <<'PY'
import datetime, json, pathlib, sys
work, out = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
report, ok = {"checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
              "scanner": "trivy 0.75.0", "images": {}}, True
for f in sorted(work.glob("*.json")):
    d = json.loads(f.read_text())
    vulns = [v for r in d.get("Results", []) for v in (r.get("Vulnerabilities") or [])]
    fixable = [v for v in vulns if v.get("FixedVersion")]
    secrets = [{"target": r["Target"], "rule": s["RuleID"]} for r in d.get("Results", []) for s in (r.get("Secrets") or [])]
    report["images"][f.stem] = {
        "os": f"{d.get('Metadata', {}).get('OS', {}).get('Family')} {d.get('Metadata', {}).get('OS', {}).get('Name')}",
        "high_critical_total": len(vulns), "fixable": [f"{v['PkgName']} {v['VulnerabilityID']}" for v in fixable],
        "unfixed": len(vulns) - len(fixable), "secrets": secrets}
    ok = ok and not fixable and not secrets
report["ok"] = ok
report["policy"] = "falha com HIGH/CRITICAL corrigível ou segredo; sem correção publicada = registrado e reavaliado a cada build"
out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
print(json.dumps({k: {kk: (vv if not isinstance(vv, list) else len(vv)) for kk, vv in v.items()} for k, v in report["images"].items()}, ensure_ascii=False))
sys.exit(0 if ok else 1)
PY
