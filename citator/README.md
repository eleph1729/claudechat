# FCL Citator

A small web app that reports the **citation status** of a case in The National
Archives' [Find Case Law](https://caselaw.nationalarchives.gov.uk/) service:
enter a neutral citation (e.g. `[2015] EWCA Civ 123`) and it finds later
judgments that cite the case, reads the passages around each citation, and
classifies the treatment (applied, followed, distinguished, doubted,
overruled, …) with Claude — showing the quoted passage as evidence for every
classification.

> **Licence required.** Find Case Law's Open Justice Licence does not permit
> computational analysis. Running this tool against the live service requires a
> [computational-analysis licence](https://caselaw.nationalarchives.gov.uk/licence-application-process)
> from The National Archives.

## How it works

1. **Parse** the neutral citation and map it to a Find Case Law document URI.
2. **Reverse lookup** — full-text search the Atom feed for the citation phrase
   (plus the case name, if supplied) to find citing judgments.
3. **Extract** — fetch each citing judgment's Akoma Ntoso XML and pull out the
   paragraphs around each reference (matching the citation text or the
   enriched `<ref>` markup).
4. **Classify** — Claude (`claude-opus-4-8`, structured output) assigns one
   treatment label per citing case with a confidence level, a verbatim
   evidence quote, and an explanation. If no Anthropic credentials are
   available it falls back to a clearly-labelled keyword heuristic.
5. **Aggregate** — the worst treatment drives an overall status banner
   (negative / caution / positive / neutral / unknown), with honest notes
   about corpus coverage.

Results are cached in `~/.cache/fcl-citator/` for a week so each case is only
analysed once.

## Running

```sh
pip install -r citator/requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...   # or `ant auth login`
python -m citator
# open http://127.0.0.1:5033/
```

Optional environment variables:

| Variable | Purpose |
|---|---|
| `CITATOR_MODEL` | Claude model ID (default `claude-opus-4-8`) |
| `FCL_BASE_URL` | Find Case Law base URL (default `https://caselaw.nationalarchives.gov.uk`) |

## Caveats

- Coverage is limited to the Find Case Law corpus (mainly senior courts of
  England & Wales from ~2001–2003, tribunals later). "No negative treatment
  found" is **not** the same as "good law".
- Search is by citation phrase and case name; judgments citing only a law
  report citation (e.g. `[2016] 1 WLR 100`) may be missed.
- Appellate history (reversed on appeal) and implicit overruling by statute
  are out of scope of a citation graph.
- Treatment classification is AI-assisted and imperfect — always read the
  quoted passages and the judgments. This is a research aid, not legal advice.

## Tests

```sh
python -m pytest tests/test_citator.py
```
