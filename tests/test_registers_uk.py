import pathlib

import pytest

from flightbridge.research.registers_uk import document_version, parse

SAMPLE = """Standards Document 31
Version 222, 31 October 2026

 Alpha Flight Training Limited (GBR.ATO.0101)

 Registered Company Number: 01234567

 Hangar 1
 Some Aerodrome
 Oxford
 OX5 1RA
 01865 000000

     •   Commercial Pilot Licence (Aeroplanes) Modular Flying
     •   Airline Transport Pilot Licence (Aeroplanes) Modular Theoretical Knowledge
         (Residential and Distance Learning)
     •   Private Pilot Licence (Aeroplanes)
31 October 2026                                                                                             Page 3 of 85
                                                      OFFICIAL - Public
                      OFFICIAL - Public. This information has been cleared for unrestricted distribution.

Approved Training Organisations Doc. 31, V222

     •   Instrument Rating (Aeroplanes) Modular Flying


 Beta Holdings Limited T/A Beta
 Academy Spain (GBR.ATO.0549)

 Registered Company Number: B82307430 (Spain)

 Aeropuerto de Jerez
 11401
 Spain

     •   Airline Transport Pilot Licence (Aeroplanes) Integrated


 Gamma Theory School (GBR.ATO.0600)

 Registered Company Number: 999 (Cyprus)

 Christchurch
 Dorset
 BH23 6BS

     •   Commercial Pilot Licence (Aeroplanes) Theoretical Knowledge
"""


def test_parse_sample():
    e = {x.approval_ref: x for x in parse(SAMPLE)}
    assert document_version(SAMPLE) == ("222", "31 October 2026")
    a = e["GBR.ATO.0101"]
    assert a.name == "Alpha Flight Training Limited" and a.phone == "01865 000000" and a.country == "GB"
    assert a.courses[1].endswith("(Residential and Distance Learning)")      # wrapped bullet joined
    assert "Instrument Rating (Aeroplanes) Modular Flying" in a.courses           # continues across a page break
    assert a.cpl_aeroplane_flying and a.ppl_aeroplane and a.instrument_rating and not a.integrated_atpl
    b = e["GBR.ATO.0549"]
    assert b.name == "Beta Holdings Limited T/A Beta Academy Spain" and b.country == "ES" and b.integrated_atpl
    assert b.cpl_aeroplane_flying                                              # integrated ATPL includes CPL flying
    g = e["GBR.ATO.0600"]
    assert g.country == "GB" and g.registered_country == "Cyprus"              # location beats registration
    assert not g.cpl_aeroplane_flying                                          # theory-only is not flying training


REAL = pathlib.Path(__file__).resolve().parents[1] / "data/registers/uk_caa/SD31_v222_2026-10-31.txt"


@pytest.mark.skipif(not REAL.exists(), reason="register text not present")
def test_real_sd31_v222_regression():
    text = REAL.read_text(encoding="utf-8")
    e = parse(text)
    assert document_version(text) == ("222", "31 October 2026")
    assert len(e) == 196 and len({x.approval_ref for x in e}) == 196
    assert sum(x.cpl_aeroplane_flying for x in e) == 35
    d = {x.approval_ref: x for x in e}
    assert d["GBR.ATO.0438"].name.startswith("Redhill Aircraft Maintenance")
    assert d["GBR.ATO.0549"].country == "ES"
    assert all(x.courses for x in e)
