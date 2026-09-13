import unittest

from concierge import bounty_audit


def _candidate(title='', body=''):
    return {'number': 1, 'title': title, 'body': body}


class RepositoryScopedReferenceTests(unittest.TestCase):
    def test_reference_forms_are_repo_scoped(self):
        repo = 'acme/widget'
        self.assertTrue(bounty_audit.references_issue(_candidate('Fix #42'), repo, 42))
        self.assertTrue(bounty_audit.references_issue(_candidate('Fix acme/widget#42'), repo, 42))
        self.assertTrue(bounty_audit.references_issue(
            _candidate(body='Closes https://github.com/acme/widget/issues/42'), repo, 42
        ))
        self.assertFalse(bounty_audit.references_issue(_candidate('Fix other/widget#42'), repo, 42))
        self.assertFalse(bounty_audit.references_issue(_candidate('Fix other/repo#42'), repo, 42))
        self.assertFalse(bounty_audit.references_issue(_candidate('Fix #420'), repo, 42))

    def test_foreign_ref_plus_real_local_ref_still_matches_local_issue(self):
        pr = _candidate(body='Related other/repo#42, but this fixes #42.')
        self.assertTrue(bounty_audit.references_issue(pr, 'acme/widget', 42))


if __name__ == '__main__':
    unittest.main()
