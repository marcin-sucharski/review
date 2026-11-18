#!/usr/bin/env python3
"""Check terminal defaults and RGB highlights in real Rex panes.

    python3 tests/live_rex_regression.py --session SESSION [--nix]

Uses only the rex CLI. Opens background tabs and closes only its own tabs.
Fixtures, HTML captures, and archives remain in the printed temporary directory.
"""
import argparse
from html.parser import HTMLParser
import json
import os
import pathlib
import subprocess as sp
import tempfile
import time

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--session', required=True)
parser.add_argument('--nix', action='store_true', help='run the local Nix flake')
args = parser.parse_args()
project = pathlib.Path(__file__).resolve().parents[1]
root = pathlib.Path(tempfile.mkdtemp(prefix='review-rex-'))
repo = root / 'repo'
repo.mkdir()
windows = []

def run(*command, cwd=repo):
    return sp.check_output(command, cwd=cwd, text=True)

def rex(*command):
    return run('rex', *command)

def call(block, method, payload=None):
    command = ['block', 'call', '-s', args.session, '-b', block, method]
    if payload is not None:
        command.append(json.dumps(payload))
    return json.loads(rex(*command))

class Colors(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = [{}]
        self.runs = []

    def handle_starttag(self, tag, attrs):
        if tag == 'div':
            style = self.stack[-1].copy()
            for declaration in dict(attrs).get('style', '').split(';'):
                key, separator, value = declaration.partition(':')
                if separator:
                    style[key.strip()] = value.replace(' ', '').lower()
            self.stack.append(style)

    def handle_endtag(self, tag):
        if tag == 'div':
            self.stack.pop()

    def handle_data(self, text):
        self.runs.append((text, self.stack[-1]))

def capture(block, marker, name, backgrounds=None):
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        html = rex('capture', '-s', args.session, '-b', block, '--format=html', '--trim')
        colors = Colors()
        colors.feed(html)
        found = marker in ''.join(text for text, _ in colors.runs)
        styled = backgrounds is None or any(
            marker in text and style.get('background-color') in backgrounds
            for text, style in colors.runs)
        if found and styled:
            (root / (name + '.html')).write_text(html)
            return colors.runs
        time.sleep(0.1)
    (root / 'FAIL.html').write_text(html)
    raise AssertionError('Timed out waiting for ' + marker)

def background(runs, marker, expected, label):
    assert any(marker in text and style.get('background-color') in expected
               for text, style in runs), (label, str(root))
    print('PASS ' + label, flush=True)

def key(block, name):
    rex('send-key', '-s', args.session, '-b', block, name)

run('git', 'init', '-q', '-b', 'main')
run('git', 'config', 'user.name', 'Rex Regression')
run('git', 'config', 'user.email', 'test@example.invalid')
(repo / 'sample.txt').write_text('context alpha\nold value\ncontext omega\n')
run('git', 'add', '.')
run('git', 'commit', '-qm', 'baseline')
run('git', 'checkout', '-qb', 'feature')
(repo / 'sample.txt').write_text('context alpha\nnew value\ncontext omega\n')
command = (['nix', 'run', 'path:' + str(project), '--'] if args.nix
           else [os.environ.get('REVIEW_BINARY', str(project / 'target/release/review'))])
print('ARTIFACTS ' + str(root), flush=True)
try:
    for scheme, no_color in [('light', False), ('dark', False), ('light', True), ('dark', True)]:
        mode = scheme + ('_no_color' if no_color else '_color')
        environment = ['env', 'NO_COLOR=1'] if no_color else ['env', '-u', 'NO_COLOR']
        environment.append('XDG_DATA_HOME=' + str(root / mode))
        created = json.loads(rex('window', 'new', '-s', args.session,
            'Review RGB regression', '-c', str(repo), '--focus=false', '--keep-open',
            '--shell=none', '--json', '--', *environment, *command, '--stdout'))
        windows.append(created['window_id'])
        block = created['block_ids'][0]
        # Only this test's pane is changed; these are the inverted entries found
        # in the user's Rex theme, rather than xterm's standard extended palette.
        call(block, 'set_theme', {'foreground': '#333940' if scheme == 'light' else '#e8edf2',
            'background': '#fafafa' if scheme == 'light' else '#101820',
            'scheme': scheme, 'palette': ['16=#fafafa', '231=#333940',
            '194=#4f554d', '224=#5e4c4f', '230=#4e473d', '229=#333940']})
        palette = call(block, 'format', {'format': 'vt', 'extra_palette': True})['content']
        (root / (mode + '_palette.vt')).write_text(palette)
        assert '\x1b]4;16;rgb:fa/fa/fa' in palette
        assert '\x1b]4;231;rgb:33/39/40' in palette
        inherited = {None}
        runs = capture(block, 'Review source', mode + '_source')
        background(runs, 'Review source', inherited, mode + ' source menu inherits terminal background')
        assert any('Review source' in text and style.get('color') is None for text, style in runs)
        key(block, 'enter')
        runs = capture(block, 'Target branch', mode + '_target')
        background(runs, 'Target branch', inherited, mode + ' target menu inherits terminal background')
        key(block, 'enter')
        runs = capture(block, 'sample.txt', mode + '_review')
        background(runs, 'sample.txt', inherited, mode + ' file header inherits terminal background')
        background(runs, 'context omega', inherited, mode + ' context inherits terminal background')
        assert any('context omega' in text and style.get('color') is None for text, style in runs)
        background(runs, 'new value', {'rgb(215,255,215)', 'rgb(215,255,175)'},
                   mode + ' addition stays light green')
        background(runs, 'old value', {'rgb(255,215,215)', 'rgb(255,215,175)'},
                   mode + ' deletion stays light red')
        key(block, 'enter')
        rex('send', '-s', args.session, '-b', block, 'Rex RGB regression note')
        runs = capture(block, 'Rex RGB regression note', mode + '_editor')
        background(runs, 'Rex RGB regression note', inherited, mode + ' editor inherits terminal background')
        key(block, 'enter')
        runs = capture(block, 'Rex RGB regression note', mode + '_comment',
                       {'rgb(255,255,175)', 'rgb(255,255,215)'})
        background(runs, 'Rex RGB regression note', {'rgb(255,255,175)', 'rgb(255,255,215)'},
                   mode + ' saved comment stays light yellow')
        rex('send', '-s', args.session, '-b', block, ':q')
        key(block, 'enter')
        deadline = time.monotonic() + 10
        while call(block, 'process')['last_exit'] is None and time.monotonic() < deadline:
            time.sleep(0.1)
        assert call(block, 'process')['last_exit']['exit_code'] == 0
        assert call(block, 'modes')['screen'] == 'primary'
        assert list((root / mode / 'review/reviews').glob('*.json'))
        print('PASS ' + mode + ' exits, restores primary screen, and archives review', flush=True)
finally:
    for window in windows:
        rex('window', 'close', '-s', args.session, window)
