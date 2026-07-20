#!/usr/bin/env bash
set -euo pipefail

BASELINE_ID='1QAgFPRx0LAPgZhufuh1OTJOKOTfXWc1Y'
BASELINE_SHA='378f1822d2f0d28e893d40dea41dd4fd98dd9c9ae54e13ccc725db5ffa55b3b8'
VERSION='0.4.0'
PACKAGE_NAME='html_offline_exporter_PageSpec_0.4.0_Dify1.7.1_SDK0.9plus.difypkg'
CLI_VERSION='0.6.1'
TAG='pagespec-0.4.0'

python -m pip install --quiet gdown pyyaml

gdown "$BASELINE_ID" -O baseline_delivery.zip
test "$(sha256sum baseline_delivery.zip | awk '{print $1}')" = "$BASELINE_SHA"
unzip -t baseline_delivery.zip >/dev/null
rm -rf baseline_delivery work package_unpacked sdk_venv dist complete
mkdir -p baseline_delivery work dist
unzip -q baseline_delivery.zip -d baseline_delivery
SOURCE_ZIP="$(find baseline_delivery -type f -name '*完整源码*vendor*.zip' | head -n 1)"
test -n "$SOURCE_ZIP"
unzip -q "$SOURCE_ZIP" -d work
PLUGIN_ROOT="$(dirname "$(find work -type f -name manifest.yaml | head -n 1)")"
test -n "$PLUGIN_ROOT"
export PLUGIN_ROOT

echo "[1/9] Apply PageSpec 0.4.0 source patch"
python - <<'PY'
from pathlib import Path
patch = Path('.ci/pagespec_040_patch.py')
text = patch.read_text(encoding='utf-8')
text = text.replace("bridge = f'''<style>", 'bridge = f"""<style>')
text = text.replace("</script>'''\n        html =", '</script>"""\n        html =')
text = text.replace("child-src 'none'; \\n        \\\"frame-src", "child-src 'none'; \\\"\\n        \\\"frame-src")
text = text.replace("child-src blob:; \\n        \\\"frame-src", "child-src blob:; \\\"\\n        \\\"frame-src")
Path('/tmp/pagespec_040_patch.py').write_text(text, encoding='utf-8')
PY
python -m py_compile /tmp/pagespec_040_patch.py .ci/pagespec_040_contract_fix.py .ci/pagespec_040_generate_workflows.py
python /tmp/pagespec_040_patch.py "$PLUGIN_ROOT"
python .ci/pagespec_040_contract_fix.py "$PLUGIN_ROOT"
cp .ci/pagespec_040_showcase_test.py "$PLUGIN_ROOT/tests/test_pagespec_040_showcase.py"

echo "[2/9] Compile and run all source tests"
find "$PLUGIN_ROOT" -type f -name '*.py' -not -path '*/vendor/*' -print0 | xargs -0 python -m py_compile
(
  cd "$PLUGIN_ROOT"
  python -m unittest discover -s tests -p 'test_*.py' | tee "$GITHUB_WORKSPACE/SOURCE_TESTS.txt"
)
find "$PLUGIN_ROOT" -type d -name '__pycache__' -prune -exec rm -rf {} +
find "$PLUGIN_ROOT" -type f -name '*.pyc' -delete

echo "[3/9] Verify frozen 172-library registry and source contract"
python - <<'PY'
from pathlib import Path
import json, os, yaml
root = Path(os.environ['PLUGIN_ROOT'])
registry = json.loads((root / 'catalog/registry.json').read_text(encoding='utf-8'))
assert [item['count'] for item in registry['volumes']] == [35, 63, 41, 33]
assert len(registry['covers']) == 172 and len(set(registry['covers'])) == 172
assert (root / 'requirements.txt').read_text(encoding='utf-8').strip() == 'dify_plugin>=0.9.0'
manifest = yaml.safe_load((root / 'manifest.yaml').read_text(encoding='utf-8'))
assert manifest['author'] == 'zhangyu' and manifest['name'] == 'html_offline_exporter'
assert str(manifest['version']) == '0.4.0'
tool = yaml.safe_load((root / 'tools/render_page.yaml').read_text(encoding='utf-8'))
forms = {item['name']: item['form'] for item in tool['parameters']}
assert forms['spec'] == 'llm' and forms['filename'] == 'llm'
assert forms['include_all_libraries'] == 'form'
assert {f'slot{i}' for i in range(1, 21)} <= set(forms)
assert tool['extra'] == {'python': {'source': 'tools/render_page.py'}}
assert not list(root.rglob('__pycache__'))
print('registry=172 counts=35/63/41/33 source-contract=PASS')
PY

echo "[4/9] Package with official stable Dify CLI ${CLI_VERSION}"
curl --fail --location --retry 5 --retry-delay 2 \
  -o dify-plugin-linux-amd64 \
  "https://github.com/langgenius/dify-plugin-daemon/releases/download/${CLI_VERSION}/dify-plugin-linux-amd64"
chmod 0755 dify-plugin-linux-amd64
./dify-plugin-linux-amd64 version | tee CLI_VERSION.txt
./dify-plugin-linux-amd64 plugin package "$PLUGIN_ROOT" \
  -o "dist/$PACKAGE_NAME" --max-size 50

echo "[5/9] Audit final package and real SDK registration"
PACKAGE="dist/$PACKAGE_NAME"
test -f "$PACKAGE"
PACKAGE_BYTES="$(stat -c%s "$PACKAGE")"
test "$PACKAGE_BYTES" -lt 15728640
unzip -t "$PACKAGE" >/dev/null
mkdir -p package_unpacked
unzip -q "$PACKAGE" -d package_unpacked
UNPACKED_BYTES="$(find package_unpacked -type f -printf '%s\n' | awk '{s+=$1} END {print s+0}')"
test "$UNPACKED_BYTES" -lt 50000000
test "$(tr -d '\r\n' < package_unpacked/requirements.txt)" = 'dify_plugin>=0.9.0'
python -m venv sdk_venv
sdk_venv/bin/pip install --quiet 'dify_plugin==0.9.1'
(
  cd package_unpacked
  ../sdk_venv/bin/python - <<'PY'
from dify_plugin import Plugin, DifyPluginEnv
plugin = Plugin(DifyPluginEnv(MAX_REQUEST_TIMEOUT=180))
assert plugin.registration is not None
print('PluginRegistration=PASS')
PY
)
printf '%s\n' "$UNPACKED_BYTES" > UNPACKED_BYTES.txt

echo "[6/9] Generate two SHA-bound modified YML workflows"
python .ci/pagespec_040_generate_workflows.py \
  --package "$PACKAGE" \
  --baseline-delivery baseline_delivery \
  --output-dir dist

echo "[7/9] Build source archive and validation report"
SOURCE_ARCHIVE='dist/PageSpec_0.4.0_完整源码_含vendor.zip'
(
  cd "$(dirname "$PLUGIN_ROOT")"
  zip -qr "$GITHUB_WORKSPACE/$SOURCE_ARCHIVE" "$(basename "$PLUGIN_ROOT")"
)
python - <<'PY'
from pathlib import Path
import hashlib, json, os, zipfile, yaml

def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

dist = Path('dist')
package = dist / 'html_offline_exporter_PageSpec_0.4.0_Dify1.7.1_SDK0.9plus.difypkg'
library = dist / 'PageSpec_城市公共图书馆年度阅读与活动报告_全库版_0.4.0_Dify1.7.1.yml'
phone = dist / '手机号一键查询并生成报告_PageSpec全库版_0.4.0_Dify1.7.1.yml'
source = dist / 'PageSpec_0.4.0_完整源码_含vendor.zip'
files = [package, library, phone, source]
rows = []
for path in files:
    assert path.is_file()
    rows.append(f'{digest(path)}  {path.name}')
    if path.suffix in {'.zip', '.difypkg'}:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            assert archive.testzip() is None
            assert len(names) == len(set(names))
            assert all('..' not in Path(name).parts and not name.startswith('/') for name in names)
(dist / 'PageSpec_0.4.0_SHA256SUMS.txt').write_text('\n'.join(rows) + '\n', encoding='utf-8')
report = {
    'version': '0.4.0',
    'plugin_identity': 'zhangyu/html_offline_exporter',
    'package_file': package.name,
    'package_bytes': package.stat().st_size,
    'package_sha256': digest(package),
    'unpacked_bytes': int(Path('UNPACKED_BYTES.txt').read_text()),
    'cli_version': Path('CLI_VERSION.txt').read_text().strip(),
    'requirements': 'dify_plugin>=0.9.0',
    'sdk_registration_version': '0.9.1',
    'catalog_counts': [35, 63, 41, 33],
    'catalog_total': 172,
    'dify_string_limit': 80000,
    'html_reject_bytes': 30000000,
    'package_limit_bytes': 15728640,
    'unpacked_limit_bytes': 50000000,
    'source_tests': Path('SOURCE_TESTS.txt').read_text(errors='replace')[-5000:],
}
(dist / 'PageSpec_0.4.0_交付验证摘要.json').write_text(
    json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8'
)
PY

echo "[8/9] Build clean complete delivery ZIP"
mkdir -p complete/安装包 complete/工作流 complete/源码 complete/验证
cp "dist/$PACKAGE_NAME" complete/安装包/
cp dist/*.yml complete/工作流/
cp dist/PageSpec_0.4.0_完整源码_含vendor.zip complete/源码/
cp dist/PageSpec_0.4.0_SHA256SUMS.txt dist/PageSpec_0.4.0_交付验证摘要.json dist/PageSpec_0.4.0_YML审计.json complete/验证/
(
  cd complete
  zip -qr ../dist/PageSpec_0.4.0_完整交付包_推荐.zip .
)
unzip -t dist/PageSpec_0.4.0_完整交付包_推荐.zip >/dev/null
sha256sum dist/PageSpec_0.4.0_完整交付包_推荐.zip > dist/PageSpec_0.4.0_完整交付包_SHA256.txt

echo "[9/9] Publish release assets"
if [[ -n "${GH_TOKEN:-}" && -n "${GITHUB_REPOSITORY:-}" ]]; then
  gh release delete "$TAG" --repo "$GITHUB_REPOSITORY" --yes --cleanup-tag >/dev/null 2>&1 || true
  gh release create "$TAG" --repo "$GITHUB_REPOSITORY" --target "${TARGET_BRANCH:-build/pagespec-0.4.0-20260720}" \
    --title 'PageSpec 0.4.0 delivery' \
    --notes 'Compatibility-preserving PageSpec 0.4.0 refactor: preserves 0.3.x JSON/YML and the frozen 172-library registry; adds isolated all-library showcase and responsive wide-table handling.'
  gh release upload "$TAG" --repo "$GITHUB_REPOSITORY" dist/* --clobber
fi

printf 'PACKAGE=%s\nPACKAGE_BYTES=%s\nUNPACKED_BYTES=%s\n' "$PACKAGE_NAME" "$PACKAGE_BYTES" "$UNPACKED_BYTES"
sha256sum dist/*
