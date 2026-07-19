import pytest

from citator.cache import Cache
from citator.citation import CitationError, parse_citation
from citator.classify import classify_heuristic
from citator.fcl import extract_citation_contexts, parse_atom_feed, parse_judgment_metadata
from citator.pipeline import aggregate


# ------------------------------------------------------------------ citations

@pytest.mark.parametrize("text,canonical,uri", [
    ("[2015] EWCA Civ 123", "[2015] EWCA Civ 123", "ewca/civ/2015/123"),
    ("[2020] EWCA Crim 7", "[2020] EWCA Crim 7", "ewca/crim/2020/7"),
    ("[2019] UKSC 41", "[2019] UKSC 41", "uksc/2019/41"),
    ("[2005] UKHL 71", "[2005] UKHL 71", "ukhl/2005/71"),
    ("[2015] UKPC 4", "[2015] UKPC 4", "ukpc/2015/4"),
    ("[2018] EWHC 2066 (Admin)", "[2018] EWHC 2066 (Admin)", "ewhc/admin/2018/2066"),
    ("[2022] EWHC 1 (Comm)", "[2022] EWHC 1 (Comm)", "ewhc/comm/2022/1"),
    ("[2023] EWHC 100 (KB)", "[2023] EWHC 100 (KB)", "ewhc/kb/2023/100"),
    ("[2021] UKUT 233 (IAC)", "[2021] UKUT 233 (IAC)", "ukut/iac/2021/233"),
    ("[2021] UKFTT 99 (TC)", "[2021] UKFTT 99 (TC)", "ukftt/tc/2021/99"),
    ("[2022] EAT 13", "[2022] EAT 13", "eat/2022/13"),
    ("[2021] EWCOP 20", "[2021] EWCOP 20", "ewcop/2021/20"),
    ("[2021] EWFC B45", "[2021] EWFC B45", "ewfc/b/2021/45"),
    ("  [2015]  ewca  civ  123 ", "[2015] EWCA Civ 123", "ewca/civ/2015/123"),
])
def test_parse_citation(text, canonical, uri):
    cit = parse_citation(text)
    assert cit.canonical == canonical
    assert cit.fcl_uri == uri


@pytest.mark.parametrize("bad", [
    "not a citation",
    "[2015] EWCA 123",        # missing Civ/Crim
    "[2015] EWHC 123",        # missing division
    "[2015] XYZ 1",           # unknown court
    "2015 EWCA Civ 123",      # missing brackets
])
def test_parse_citation_rejects(bad):
    with pytest.raises(CitationError):
        parse_citation(bad)


def test_context_regex_tolerates_spacing():
    cit = parse_citation("[2015] EWCA Civ 123")
    assert cit.context_regex().search("see [2015]  EWCA   Civ 123 at [45]")
    assert cit.context_regex().search("[ 2015 ] EWCA Civ 123")
    assert not cit.context_regex().search("[2015] EWCA Civ 1234")


# ------------------------------------------------------------------ atom feed

ATOM_SAMPLE = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Search results</title>
  <entry>
    <title>Alpha v Beta</title>
    <link rel="alternate" href="https://caselaw.nationalarchives.gov.uk/ewhc/admin/2020/99"/>
    <published>2020-05-01T00:00:00Z</published>
  </entry>
  <entry>
    <title>Gamma v Delta</title>
    <link href="https://caselaw.nationalarchives.gov.uk/uksc/2021/5"/>
  </entry>
</feed>"""


def test_parse_atom_feed():
    hits = parse_atom_feed(ATOM_SAMPLE)
    assert [h.uri for h in hits] == ["ewhc/admin/2020/99", "uksc/2021/5"]
    assert hits[0].title == "Alpha v Beta"
    assert hits[0].date == "2020-05-01T00:00:00Z"


# ------------------------------------------------------------ judgment parsing

AKN_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0"
            xmlns:uk="https://caselaw.nationalarchives.gov.uk/akn">
  <judgment name="judgment">
    <meta>
      <identification source="#tna">
        <FRBRWork>
          <FRBRname value="Gamma v Delta"/>
          <FRBRdate date="2021-03-04" name="judgment"/>
        </FRBRWork>
      </identification>
      <proprietary source="#"><uk:cite>[2021] UKSC 5</uk:cite></proprietary>
    </meta>
    <judgmentBody>
      <paragraph><content><p>1. This appeal concerns limitation periods.</p></content></paragraph>
      <paragraph><content><p>2. In <ref uk:type="case"
        href="https://caselaw.nationalarchives.gov.uk/ewca/civ/2015/123">Smith v Jones
        [2015] EWCA Civ 123</ref> the Court of Appeal held otherwise.</p></content></paragraph>
      <paragraph><content><p>3. We consider that Smith v Jones was wrongly decided
        and must be overruled.</p></content></paragraph>
      <paragraph><content><p>4. For these reasons the appeal is allowed.</p></content></paragraph>
      <paragraph><content><p>5. An unrelated point about costs.</p></content></paragraph>
    </judgmentBody>
  </judgment>
</akomaNtoso>"""


def test_parse_judgment_metadata():
    name, cite, date = parse_judgment_metadata(AKN_SAMPLE)
    assert name == "Gamma v Delta"
    assert cite == "[2021] UKSC 5"
    assert date == "2021-03-04"


def test_extract_citation_contexts():
    target = parse_citation("[2015] EWCA Civ 123")
    contexts = extract_citation_contexts(AKN_SAMPLE, target)
    assert len(contexts) == 1
    # window of ±1 paragraph around the citing paragraph
    assert "limitation periods" in contexts[0]
    assert "[2015] EWCA Civ 123" in contexts[0]
    assert "wrongly decided" in contexts[0]
    assert "unrelated point" not in contexts[0]


def test_extract_contexts_matches_ref_href_only():
    xml = AKN_SAMPLE.replace("Smith v Jones\n        [2015] EWCA Civ 123", "Smith v Jones")
    target = parse_citation("[2015] EWCA Civ 123")
    contexts = extract_citation_contexts(xml, target)
    assert len(contexts) == 1
    assert "Court of Appeal held otherwise" in contexts[0]


def test_extract_contexts_none_when_absent():
    target = parse_citation("[1999] UKHL 1")
    assert extract_citation_contexts(AKN_SAMPLE, target) == []


# --------------------------------------------------------------- classification

def test_heuristic_finds_overruling():
    result = classify_heuristic(["We consider that Smith was wrongly decided and is overruled."])
    assert result["treatment"] == "overruled"
    assert result["polarity"] == "negative"
    assert result["severity"] == 3
    assert result["method"] == "heuristic"


def test_heuristic_distinguished():
    result = classify_heuristic(["The present facts are different; Smith is distinguished."])
    assert result["treatment"] == "distinguished"
    assert result["severity"] == 1


def test_heuristic_default_cited():
    result = classify_heuristic(["See Smith v Jones [2015] EWCA Civ 123."])
    assert result["treatment"] == "cited"
    assert result["severity"] == 0


# ------------------------------------------------------------------ aggregation

def _c(treatment, severity, polarity):
    return {"treatment": treatment, "severity": severity, "polarity": polarity}


def test_aggregate():
    assert aggregate([]) == "unknown"
    assert aggregate([_c("cited", 0, "neutral")]) == "neutral"
    assert aggregate([_c("applied", 0, "positive"), _c("cited", 0, "neutral")]) == "positive"
    assert aggregate([_c("distinguished", 1, "caution")]) == "caution"
    assert aggregate([_c("doubted", 2, "negative"), _c("applied", 0, "positive")]) == "negative"
    assert aggregate([_c("overruled", 3, "negative")]) == "negative"


# ------------------------------------------------------------------------ cache

def test_cache_roundtrip(tmp_path):
    cache = Cache(path=tmp_path / "c.db")
    assert cache.get("[2015] EWCA Civ 123") is None
    cache.set("[2015] EWCA Civ 123", {"overall": "positive"})
    assert cache.get("[2015] EWCA Civ 123") == {"overall": "positive"}


def test_cache_expiry(tmp_path):
    cache = Cache(path=tmp_path / "c.db", ttl=0)
    cache.set("k", {"v": 1})
    assert cache.get("k") is None
