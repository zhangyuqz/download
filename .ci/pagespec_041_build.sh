#!/usr/bin/env bash
set -euo pipefail

REPO="${GITHUB_REPOSITORY:-zhangyuqz/download}"
VERSION="0.4.1"
BASE_TAG="pagespec-0.4.0"
TAG="pagespec-${VERSION}"
SOURCE_SHA="04096a862e7c9d16d3a430666797b648f68cc74d3332f369b55e17969d189aa6"
LIB_YML_SHA="8fd456f52b96beecfc51936afef994c3d3858df1c814b01e1a3ae306d9b72647"
PHONE_YML_SHA="77650a4eaad33bed441b8682b81a6e3c6d07b67e3034dc28a4759f8875407eb9"
PACKAGE="html_offline_exporter_PageSpec_${VERSION}_Dify1.7.1_SDK0.9plus.difypkg"
LIB_YML="pagespec_library_report_full_${VERSION}_dify_1.7.1.yml"
PHONE_YML="pagespec_phone_report_full_${VERSION}_dify_1.7.1.yml"
SOURCE_OUT="pagespec_${VERSION}_source_with_vendor.zip"
DELIVERY="pagespec_${VERSION}_complete_delivery.zip"
SUMS="pagespec_${VERSION}_sha256sums.txt"
VALIDATION="pagespec_${VERSION}_validation_summary.json"

rm -rf baseline041 work041 dist041 package_unpack sdk_min sdk_current browser041
mkdir -p baseline041/source baseline041/yml dist041 browser041

echo '[1/11] Download verified PageSpec 0.4.0 source and workflows'
gh release download "$BASE_TAG" --repo "$REPO" --pattern '*vendor.zip' --dir baseline041/source
SOURCE_ARCHIVE="$(find baseline041/source -maxdepth 1 -type f -name '*.zip' | head -n 1)"
test -n "$SOURCE_ARCHIVE"
test "$(sha256sum "$SOURCE_ARCHIVE" | awk '{print $1}')" = "$SOURCE_SHA"
unzip -q "$SOURCE_ARCHIVE" -d work041
PLUGIN_ROOT="$(dirname "$(find work041 -type f -name manifest.yaml | head -n 1)")"
test -n "$PLUGIN_ROOT"

gh release download "$BASE_TAG" --repo "$REPO" --pattern '*.yml' --dir baseline041/yml
BASE_LIB=''; BASE_PHONE=''
while IFS= read -r file; do
  digest="$(sha256sum "$file" | awk '{print $1}')"
  if [ "$digest" = "$LIB_YML_SHA" ]; then BASE_LIB="$file"; fi
  if [ "$digest" = "$PHONE_YML_SHA" ]; then BASE_PHONE="$file"; fi
done < <(find baseline041/yml -maxdepth 1 -type f -name '*.yml' | sort)
test -n "$BASE_LIB" && test -n "$BASE_PHONE"

echo '[2/11] Apply focused 0.4.1 fixes'
python -m pip install --quiet pyyaml
python .ci/pagespec_041_patch.py "$PLUGIN_ROOT"
find "$PLUGIN_ROOT" -type f -name '*.py' -not -path '*/vendor/*' -print0 | xargs -0 python -m py_compile

echo '[3/11] Run complete source and regression test suite'
(
  cd "$PLUGIN_ROOT"
  python -m unittest discover -s tests -p 'test_*.py'
) | tee dist041/source_tests.log

python - "$PLUGIN_ROOT" <<'PY'
import json,pathlib,sys,yaml
root=pathlib.Path(sys.argv[1])
manifest=yaml.safe_load((root/'manifest.yaml').read_text(encoding='utf-8'))
assert manifest['version']=='0.4.1'
assert manifest['author']=='zhangyu' and manifest['name']=='html_offline_exporter'
assert manifest['meta']['minimum_dify_version']=='1.7.1'
assert (root/'requirements.txt').read_text(encoding='utf-8').strip()=='dify_plugin>=0.9.0'
registry=json.loads((root/'catalog/registry.json').read_text(encoding='utf-8'))
assert len(registry['covers'])==172 and len(set(registry['covers']))==172
assert [item['count'] for item in registry['volumes']]==[35,63,41,33]
print('source-contract=PASS libraries=172 counts=35/63/41/33')
PY

find "$PLUGIN_ROOT" -type d -name '__pycache__' -prune -exec rm -rf {} +
find "$PLUGIN_ROOT" -type f \( -name '*.pyc' -o -name '*.pyo' -o -name '*.log' \) -delete

echo '[4/11] Package with current official stable Dify CLI'
CLI_TAG="$(curl -fsSL https://api.github.com/repos/langgenius/dify-plugin-daemon/releases/latest | python -c 'import json,sys; print(json.load(sys.stdin)["tag_name"])')"
test -n "$CLI_TAG"
curl -fsSL --retry 5 --retry-delay 2 -o dify-plugin-linux-amd64 "https://github.com/langgenius/dify-plugin-daemon/releases/download/${CLI_TAG}/dify-plugin-linux-amd64"
chmod 0755 dify-plugin-linux-amd64
./dify-plugin-linux-amd64 version | tee dist041/official_cli_version.txt
./dify-plugin-linux-amd64 plugin package "$PLUGIN_ROOT" -o "dist041/$PACKAGE"
test -f "dist041/$PACKAGE"

echo '[5/11] Audit package structure, size and source parity'
test "$(stat -c%s "dist041/$PACKAGE")" -lt 15728640
unzip -t "dist041/$PACKAGE" >/dev/null
mkdir package_unpack
unzip -q "dist041/$PACKAGE" -d package_unpack
UNPACKED_BYTES="$(find package_unpack -type f -printf '%s\n' | awk '{s+=$1}END{print s+0}')"
test "$UNPACKED_BYTES" -lt 50000000
python - "$PLUGIN_ROOT" package_unpack <<'PY'
import pathlib,sys
source=pathlib.Path(sys.argv[1]);package=pathlib.Path(sys.argv[2])
for path in package.rglob('*'):
    if not path.is_file(): continue
    rel=path.relative_to(package)
    counterpart=source/rel
    assert counterpart.is_file(),f'missing source file: {rel}'
    assert path.read_bytes()==counterpart.read_bytes(),f'package/source mismatch: {rel}'
for path in source.rglob('*'):
    if path.is_file():
        rel=path.relative_to(source).as_posix().lower()
        assert '__pycache__' not in rel and not rel.endswith(('.pyc','.pyo','.log'))
print('package-source-parity=PASS')
PY

echo '[6/11] Test minimum and currently resolved dify_plugin runtimes'
python -m venv sdk_min
sdk_min/bin/pip install --quiet 'dify_plugin==0.9.0'
(
 cd package_unpack
 ../sdk_min/bin/python - <<'PY'
from dify_plugin import Plugin,DifyPluginEnv
plugin=Plugin(DifyPluginEnv(MAX_REQUEST_TIMEOUT=180));assert plugin.registration is not None
print('dify_plugin=0.9.0 PluginRegistration=PASS')
PY
) | tee dist041/sdk_minimum.log
python -m venv sdk_current
sdk_current/bin/pip install --quiet -r package_unpack/requirements.txt
sdk_current/bin/python -c 'import importlib.metadata; print(importlib.metadata.version("dify_plugin"))' | tee dist041/sdk_resolved_version.txt
(
 cd package_unpack
 ../sdk_current/bin/python - <<'PY'
from dify_plugin import Plugin,DifyPluginEnv
plugin=Plugin(DifyPluginEnv(MAX_REQUEST_TIMEOUT=180));assert plugin.registration is not None
print('resolved-sdk PluginRegistration=PASS')
PY
) | tee dist041/sdk_current.log
set +e
(
 cd package_unpack
 timeout 5s ../sdk_current/bin/python -m main
) > dist041/sdk_stdio.stdout 2> dist041/sdk_stdio.stderr
STDIO_STATUS=$?
set -e
if [ "$STDIO_STATUS" != 0 ] && [ "$STDIO_STATUS" != 124 ]; then exit "$STDIO_STATUS"; fi
test ! -s dist041/sdk_stdio.stderr

echo '[7/11] Generate SHA-bound 0.4.1 workflows with ASCII release names'
python - "$BASE_LIB" "$BASE_PHONE" "dist041/$PACKAGE" "dist041/$LIB_YML" "dist041/$PHONE_YML" <<'PY'
import copy,hashlib,pathlib,re,sys,yaml
base_lib,base_phone,package,out_lib,out_phone=map(pathlib.Path,sys.argv[1:])
sha=hashlib.sha256(package.read_bytes()).hexdigest();identifier=f'zhangyu/html_offline_exporter:0.4.1@{sha}'
pattern=re.compile(r'zhangyu/html_offline_exporter:[0-9.]+@[0-9a-f]{64}')
def walk_strings(value):
    if isinstance(value,str): yield value
    elif isinstance(value,list):
        for item in value: yield from walk_strings(item)
    elif isinstance(value,dict):
        for item in value.values(): yield from walk_strings(item)
def replace(value):
    if isinstance(value,str): return pattern.sub(identifier,value)
    if isinstance(value,list): return [replace(item) for item in value]
    if isinstance(value,dict): return {key:replace(item) for key,item in value.items()}
    return value
def normalized(value):
    value=copy.deepcopy(value)
    value['app']['name']='APP';value['app']['description']='DESC'
    value=replace_identity(value)
    return value
def replace_identity(value):
    if isinstance(value,str): return pattern.sub('zhangyu/html_offline_exporter:VERSION@SHA',value)
    if isinstance(value,list): return [replace_identity(item) for item in value]
    if isinstance(value,dict): return {key:replace_identity(item) for key,item in value.items()}
    return value
def build(source,out,name,desc,expected_nodes):
    before=yaml.safe_load(source.read_text(encoding='utf-8'));doc=replace(copy.deepcopy(before))
    doc['app']['name']=name;doc['app']['description']=desc
    nodes=[]
    for node in doc['workflow']['graph']['nodes']:
        data=node.get('data') or {}
        if data.get('type')=='tool' and data.get('tool_name')=='render_page':
            data.setdefault('tool_configurations',{})['include_all_libraries']={'type':'constant','value':True};nodes.append(node)
    assert len(nodes)==expected_nodes
    assert all(((node['data']['tool_configurations']['include_all_libraries']).get('value') is True) for node in nodes)
    strings=list(walk_strings(doc));assert max(map(len,strings),default=0)<80000
    # Existing 0.4.0 full-library YML already had the switch; after normalising
    # identity/app metadata, 0.4.1 must remain structurally identical.
    before_norm=copy.deepcopy(before);before_norm['app']['name']='APP';before_norm['app']['description']='DESC';before_norm=replace_identity(before_norm)
    after_norm=copy.deepcopy(doc);after_norm['app']['name']='APP';after_norm['app']['description']='DESC';after_norm=replace_identity(after_norm)
    assert before_norm==after_norm
    out.write_text(yaml.safe_dump(doc,allow_unicode=True,sort_keys=False,width=1000),encoding='utf-8')
    return {'bytes':out.stat().st_size,'maximum_string':max(map(len,strings),default=0),'render_nodes':len(nodes)}
a=build(base_lib,out_lib,'城市公共图书馆年度阅读与活动报告·PageSpec 0.4.1','修复编码容错与全库卷 CSP/进度协议；同一离线 HTML 展示 172 库。',1)
b=build(base_phone,out_phone,'手机号一键查询并生成报告·PageSpec 0.4.1','保留原业务节点与双报告分支，修复编码容错与全库卷执行。',2)
pathlib.Path('dist041/plugin_identifier.txt').write_text(identifier+'\n',encoding='utf-8')
pathlib.Path('dist041/workflow_audit.json').write_text(__import__('json').dumps({'identifier':identifier,'library':a,'phone':b},ensure_ascii=False,indent=2),encoding='utf-8')
print(identifier);print(a);print(b)
PY

echo '[8/11] Run final desktop/native and mobile/pako browser audit'
python -m pip install --quiet playwright
python -m playwright install --with-deps chromium >/dev/null
python .ci/pagespec_041_browser_audit.py --package "dist041/$PACKAGE" --html browser041/pagespec_041_browser_audit.html --output dist041/browser_audit.json

echo '[9/11] Build clean source and complete delivery archives'
find "$PLUGIN_ROOT" -type d -name '__pycache__' -prune -exec rm -rf {} +
find "$PLUGIN_ROOT" -type f \( -name '*.pyc' -o -name '*.pyo' -o -name '*.log' \) -delete
(cd "$(dirname "$PLUGIN_ROOT")" && zip -qr "$GITHUB_WORKSPACE/dist041/$SOURCE_OUT" "$(basename "$PLUGIN_ROOT")")

mkdir -p bundle041
cp "dist041/$PACKAGE" "bundle041/$PACKAGE"
cp "dist041/$LIB_YML" "bundle041/$LIB_YML"
cp "dist041/$PHONE_YML" "bundle041/$PHONE_YML"
cp "dist041/$SOURCE_OUT" "bundle041/$SOURCE_OUT"
cp "dist041/browser_audit.json" bundle041/browser_audit.json
cp dist041/source_tests.log dist041/official_cli_version.txt dist041/sdk_minimum.log dist041/sdk_resolved_version.txt dist041/sdk_current.log dist041/plugin_identifier.txt dist041/workflow_audit.json bundle041/
cp "dist041/$LIB_YML" 'bundle041/PageSpec_城市公共图书馆年度阅读与活动报告_全库版_0.4.1_Dify1.7.1.yml'
cp "dist041/$PHONE_YML" 'bundle041/手机号一键查询并生成报告_PageSpec全库版_0.4.1_Dify1.7.1.yml'

python - "$PLUGIN_ROOT" "dist041/$PACKAGE" "$UNPACKED_BYTES" "$CLI_TAG" "dist041/$VALIDATION" <<'PY'
import hashlib,json,pathlib,sys,zipfile
root,package,unpacked,cli,output=pathlib.Path(sys.argv[1]),pathlib.Path(sys.argv[2]),int(sys.argv[3]),sys.argv[4],pathlib.Path(sys.argv[5])
with zipfile.ZipFile(package) as archive:
    infos=[item for item in archive.infolist() if not item.is_dir()];bad=archive.testzip();assert bad is None
    maximum=max(infos,key=lambda item:item.file_size)
report={'version':'0.4.1','cli_tag':cli,'package':{'name':package.name,'bytes':package.stat().st_size,'sha256':hashlib.sha256(package.read_bytes()).hexdigest(),'members':len(infos),'unpacked_bytes':unpacked,'compressed_margin_bytes':15728640-package.stat().st_size,'unpacked_margin_bytes':50000000-unpacked,'maximum_member':maximum.filename,'maximum_member_bytes':maximum.file_size},'source_files':sum(1 for p in root.rglob('*') if p.is_file()),'browser_audit':json.loads(pathlib.Path('dist041/browser_audit.json').read_text(encoding='utf-8'))}
assert report['browser_audit']['passed'] is True
output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report['package'],ensure_ascii=False))
PY
cp "dist041/$VALIDATION" bundle041/

(
 cd bundle041
 sha256sum * > "$SUMS"
)
cp "bundle041/$SUMS" "dist041/$SUMS"
cp "bundle041/$SUMS" "bundle041/$SUMS.copy"
mv "bundle041/$SUMS.copy" "bundle041/$SUMS"
(cd bundle041 && zip -qr "$GITHUB_WORKSPACE/dist041/$DELIVERY" .)
unzip -t "dist041/$SOURCE_OUT" >/dev/null
unzip -t "dist041/$DELIVERY" >/dev/null

echo '[10/11] Validate final hashes and ASCII release names'
python - dist041 <<'PY'
import pathlib,re,sys,zipfile
root=pathlib.Path(sys.argv[1]);files=[p for p in root.iterdir() if p.is_file()]
for path in files:
    assert path.name.isascii(),f'non-ASCII release asset: {path.name}'
for path in files:
    if path.suffix in {'.zip','.difypkg'}:
        with zipfile.ZipFile(path) as archive: assert archive.testzip() is None
assert all(not re.search(r'[\u3400-\u9fff]',p.name) for p in files)
print('ascii-release-assets=PASS',sorted(p.name for p in files))
PY

echo '[11/11] Publish PageSpec 0.4.1 release and print exact identity'
if gh release view "$TAG" --repo "$REPO" >/dev/null 2>&1; then
  echo "release $TAG already exists" >&2; exit 1
fi
gh release create "$TAG" --repo "$REPO" --target "${GITHUB_SHA:-build/pagespec-0.4.1-20260721}" --title 'PageSpec 0.4.1' --notes 'Fixes encoded transport recovery, catalogue child CSP execution, progress-aware timeouts, pako fallback, and release filename portability.' \
  "dist041/$PACKAGE" "dist041/$LIB_YML" "dist041/$PHONE_YML" "dist041/$SOURCE_OUT" "dist041/$DELIVERY" "dist041/$SUMS" "dist041/$VALIDATION" dist041/browser_audit.json dist041/workflow_audit.json

sha256sum dist041/* | sort | tee dist041/all_release_sha256.txt
echo "CLI_TAG=$CLI_TAG"
echo "PACKAGE_BYTES=$(stat -c%s "dist041/$PACKAGE")"
echo "UNPACKED_BYTES=$UNPACKED_BYTES"
