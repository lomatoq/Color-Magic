"""Standalone: python -m unittest discover -s tests -p test_updater.py"""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

spec = importlib.util.spec_from_file_location('update_core', Path(__file__).resolve().parents[1] / 'source/color_prime/update_core.py')
core = importlib.util.module_from_spec(spec); spec.loader.exec_module(core)


def package(extra=None, corrupt=False):
    files = {'__init__.py': b'# new addon', 'test.py': b'# valid'}
    manifest = {'version': '3.5.2', 'files': {n: hashlib.sha256(v).hexdigest() for n, v in files.items()}}
    if corrupt: files['test.py'] = b'# corrupt'
    result = io.BytesIO()
    with zipfile.ZipFile(result, 'w') as archive:
        for name, data in files.items(): archive.writestr('color_prime/' + name, data)
        archive.writestr('color_prime/package_integrity.json', json.dumps(manifest))
        if extra: archive.writestr(*extra)
    return result.getvalue()


class UpdaterTests(unittest.TestCase):
    def test_versions(self):
        self.assertEqual(core.version('v3.5.2'), (3, 5, 2))
        for tag in ('main', 'v3.5.2-beta', '../../x'):
            with self.assertRaises(ValueError): core.version(tag)

    def test_valid_archive(self):
        self.assertTrue(core.validate(package(), (3, 5, 2)))

    def test_reject_bad_archives(self):
        for data in (package(('color_prime/../outside', 'bad')), package(('color_prime/evil.py', 'bad')), package(corrupt=True), package(('color_prime/..\\outside', 'bad'))):
            with self.assertRaises(ValueError): core.validate(data, (3, 5, 2))
        with self.assertRaises(ValueError): core.validate(package(), (3, 6, 0))

    def test_reject_foreign_url(self):
        with self.assertRaises(ValueError): core.download('https://example.org/addon.zip')

    def test_checksum(self):
        with patch.object(core, 'download', side_effect=[package(), b'0' * 64]):
            with self.assertRaises(ValueError): core.fetch_package({'zip':'zip','checksum':'sha','version':(3,5,2)})

    def test_install_backup(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'color_prime'; target.mkdir()
            (target / '__init__.py').write_text('# old')
            (target / 'obsolete.py').write_text('# obsolete')
            backup = core.install(package(), target, (3, 5, 2))
            self.assertEqual((target/'__init__.py').read_bytes(), b'# new addon')
            self.assertFalse((target/'obsolete.py').exists())
            with zipfile.ZipFile(backup) as archive:
                self.assertEqual(archive.read('color_prime/__init__.py'), b'# old')

    def test_rollback(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'color_prime'; target.mkdir()
            (target/'__init__.py').write_text('# original')
            rename = Path.rename
            def fail_new(path, dest):
                if path.name == 'next': raise OSError('simulated access error')
                return rename(path, dest)
            with patch.object(Path, 'rename', fail_new):
                with self.assertRaises(OSError): core.install(package(), target, (3,5,2))
            self.assertEqual((target/'__init__.py').read_text(), '# original')

    def test_failed_download(self):
        with patch.object(core, 'download', side_effect=OSError('offline')):
            with self.assertRaises(OSError): core.latest()


if __name__ == '__main__': unittest.main()
