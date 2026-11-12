"""Entrada dos comandos BeeGuard; execute python run.py --help."""
import os
from pathlib import Path
import runpy
import sys
import unittest

ROOT = Path(__file__).resolve().parent


def main():
    commands = {p.stem: p for p in (ROOT / 'src').glob('*.py')}
    commands.update({p.stem: p for p in (ROOT / 'src/acquisition').glob('*.py')
                     if p.stem != '__init__'})
    command = sys.argv[1] if len(sys.argv) > 1 else 'beeguard_web'
    if command in ('-h', '--help'):
        print('Uso: python run.py [comando] [opções]\nPadrão: beeguard_web\n'
              + '\n'.join(sorted(commands)) + '\ntest')
        return
    if command != 'test' and command not in commands:
        raise SystemExit(f'Comando desconhecido: {command}. Use --help.')
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT / 'src'))
    if command == 'test':
        suite = unittest.defaultTestLoader.discover(str(ROOT / 'tests'))
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        raise SystemExit(0 if result.wasSuccessful() else 1)
    sys.argv = [str(commands[command]), *sys.argv[2:]]
    runpy.run_path(str(commands[command]), run_name='__main__')


if __name__ == '__main__':
    main()
