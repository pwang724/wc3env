#!/usr/bin/env bash
set -euo pipefail
python='C:\Python311\python.exe'
if [ "${1:-environment}" = environment ]; then
    python3 /opt/worker/platform_probe.py
    timeout 120s wineboot -u
    timeout 30s wine 'C:\windows\syswow64\cmd.exe' /c echo 'Windows x86 process: OK'
    timeout 240s wine 'Z:\opt\python-installer.exe' /quiet InstallAllUsers=0 \
        'TargetDir=C:\Python311' Include_launcher=0 Include_test=0 Include_doc=0 \
        Include_tcltk=0 Include_pip=1
fi
for wheel in /opt/wheels/"${1:-environment}"/*.whl; do
    timeout 120s wine "$python" -m pip install --no-index --no-deps "$(winepath -w "$wheel")"
done
timeout 30s wine "$python" -c 'import struct; assert struct.calcsize("P") == 8; import wc3env'
wineserver -k
timeout 30s wineserver -w
