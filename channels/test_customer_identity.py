import unittest

from bridge import Held
from customer_identity import fingerprint, resolve


class Identity(unittest.TestCase):
    def test_batch_requires_current_unique_profile_and_native_recipient(self):
        numbers=["5511960000001","5511960000002","5511960000003","5511960000004","5511960000005"]
        def jid(n):
            return n[:4]+n[5:]+"@s.whatsapp.net"
        rows=[{"Codigo":str(i+1),"Cli_Celular":n} for i,n in enumerate(numbers)]
        rows[0].update(PRe_Ativo=0,Obs="Não enviar mensagens")
        rows.append({**rows[1],"Codigo":"100"})
        rows.append({**rows[2],"Cli_Celular":"5511960000066"})
        directory={"data":rows,"recordsTotal":len(rows),"recordsFiltered":len(rows)}
        input_jids=[jid(n) for n in numbers]+["551160000099@s.whatsapp.net"]
        def lookup(batch):
            self.assertEqual(batch,[numbers[i] for i in (0,3,4)])
            return [{"exists":True,"number":numbers[0],"jid":jid(numbers[0])},
                    {"exists":True,"number":numbers[3],"jid":jid(numbers[2])}]
        decisions=resolve(directory,[fingerprint(k) for k in input_jids],lookup)
        observed=[decisions[fingerprint(k)] for k in input_jids]
        self.assertEqual([r["status"] for r in observed],["identified","ambiguous_customer_match","ambiguous_customer_match",
            "provider_identity_unconfirmed","provider_identity_unconfirmed","no_customer_match"])
        self.assertTrue(observed[0]["inactive"] and observed[0]["opt_out"])
        self.assertEqual(observed[0]["matched_by"],"provider_verified_variant")
        for bad in ({"recordsTotal":99},{"recordsFiltered":None},{"erro":"expired"}):
            with self.assertRaises(Held):
                resolve({**directory,**bad},[fingerprint(input_jids[0])],lookup)
        with self.assertRaises(Held):
            resolve(directory,[fingerprint(input_jids[0])],lambda batch:[{"number":"unexpected"}])


if __name__=="__main__":
    unittest.main()
