#!/usr/bin/env python3
"""Build a self-contained view served beside the latest in-memory snapshots."""
import argparse
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
MARKER = '__MACHINES_LATEST_SNAPSHOT__'


def safe_json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')


def build():
    content = (ROOT / '_includes/machines-content.html').read_text()
    inventory = json.loads((ROOT / 'assets/machines/inventory.json').read_text())
    css = '\n'.join((ROOT / path).read_text() for path in ('assets/css/site.css', 'assets/css/machines.css'))
    config = {'statusUrl': '/machines-api/status', 'apiBase': '/machines-api', 'requireAuth': True, 'statusUrls': [], 'refreshMs': 120000,
              'staleAfterSeconds': 300, 'reportingPending': False}
    script = (ROOT / 'assets/js/machines.js').read_text()
    management = (ROOT / 'assets/js/machines-management.js').read_text()
    return ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<meta name="robots" content="noindex,nofollow,noarchive">'
            '<title>RUC-Nova GPU 资源管理平台</title><style>' + css + '</style></head>'
            '<body class="machines-page"><main>' + content + '</main>'
            '<script id="machines-inventory" type="application/json">' + safe_json(inventory) + '</script>'
            '<script id="machines-initial-status" type="application/json">' + MARKER + '</script>'
            '<script id="machines-initial-session" type="application/json">__MACHINES_INITIAL_SESSION__</script>'
            '<script>window.MACHINES_CONFIG = ' + safe_json(config) + ';\n' + script + '\n' + management + '</script>'
            '</body></html>')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=pathlib.Path, required=True)
    args = parser.parse_args()
    args.output.write_text(build())
