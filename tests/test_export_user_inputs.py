import json
import tempfile
import unittest
from pathlib import Path

from export_user_inputs import export_inputs


class ExportUserInputsTests(unittest.TestCase):
    def test_export_nested_content_metadata_and_full_record_dedup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / '20' / '10' / '05'
            data.mkdir(parents=True)
            first = {'start': 1, 'spanAttributes': {'content': '用户输入\n完整内容'},
                     'resourceAttributes': {'host.name': 'host-a', 'user.id': 'user-a'}}
            second = {**first, 'start': 2}
            (data / 'a.json').write_text(json.dumps([first, first, second, {'spanAttributes': {}}]))
            result = export_inputs(root)
            exported = json.loads((root / 'user_inputs.json').read_text())
            self.assertEqual(result['exported'], 3)
            self.assertEqual(exported[0]['content'], '用户输入\n完整内容')
            self.assertEqual(exported[0]['user_id'], 'user-a')
            self.assertEqual(exported[0]['host_name'], 'host-a')
            result = export_inputs(root, deduplicate=True)
            self.assertEqual(result['exported'], 2)
            self.assertEqual(result['duplicates'], 1)

    def test_jsonl_structured_empty_values_and_bad_lines(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'input.jsonl'
            path.write_text('\n'.join(json.dumps({'spanAttributes': {'content': value}})
                                      for value in [{'text': '完整对象'}, '', None]) + '\nnot-json\n')
            result = export_inputs(path)
            output = json.loads(Path(result['output']).read_text())
            self.assertEqual([row['content'] for row in output], [{'text': '完整对象'}, '', None])
            self.assertTrue(result['warnings'])
            with self.assertRaises(ValueError):
                export_inputs(path, output=path)

    def test_invalid_file_does_not_replace_previous_export(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'invalid.json'
            source.write_text('invalid')
            target = root / 'user_inputs.json'
            target.write_text('["previous"]')
            with self.assertRaises(ValueError):
                export_inputs(source)
            self.assertEqual(target.read_text(), '["previous"]')
            self.assertFalse(list(root.glob('.user_inputs-*.tmp')))


if __name__ == '__main__':
    unittest.main()
