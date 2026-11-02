import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
from normalize import normalize
class Normalize(unittest.TestCase):
    def test_vectors(self):
        self.assertEqual(normalize('  设计稿  '), ('设计稿', '设计稿'))
        self.assertEqual(normalize('ExampleModel'), ('ExampleModel', 'examplemodel'))
        self.assertEqual(normalize('Ａ\t B\u3000C'), ('A B C', 'a b c'))
    def test_reject(self):
        for value in (None, 'a'*81, '\0', '', 'x\u200by', 'ß'*41):
            with self.subTest(value_type=type(value).__name__), self.assertRaises(ValueError):
                normalize(value)
