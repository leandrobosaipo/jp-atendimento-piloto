import unittest
from unittest.mock import patch
import partial_directory as directory


class DirectoryTests(unittest.TestCase):
    def test_missing_or_partial_rows_do_not_authorize_recovery(self):
        sentinel = dict(Codigo='',Cli_Codigo='',Celular='',DT_RowId=0)
        empty = dict(recordsTotal=0,recordsFiltered=0,data=sentinel)
        self.assertTrue(directory.empty_directory(empty))
        for changed in [dict(data=[]),dict(recordsTotal=1),dict(data={}),dict(erro=1),
                        dict(data={**sentinel,'Codigo':'12'})]:
            self.assertFalse(directory.empty_directory({**empty,**changed}))
        with patch.object(directory,'report',side_effect=[empty,dict(data=[])]):
            with self.assertRaisesRegex(ValueError,'identity_unverified'):
                directory.partial_failure({},[])

    def test_candidate_needs_complete_directory_containing_the_verified_clients(self):
        good = dict(data=[dict(Codigo=i) for i in range(1,101)],recordsTotal=100,recordsFiltered=100)
        with patch.object(directory,'identity',return_value={'1','90'}):
            for doc in [{**good,'recordsFiltered':101}, {**good,'data':[]},
                        {**good,'data':[dict(Codigo=i) for i in range(10,110)]}]:
                with patch.object(directory,'report',return_value=doc), self.assertRaises(ValueError):
                    directory.verified_directory({},[])
            with patch.object(directory,'report',return_value=good):
                self.assertEqual(directory.verified_directory({},[]),100)
