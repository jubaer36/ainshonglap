# AI usage

## Tools

- **Claude Code**, mostly with **Claude Sonnet 5** on high effort: writing the
  code (backend, frontend, pipeline and experiment scripts).
- **ChatGPT, Gemini, Claude and DeepSeek**: research, comparing options, and
  finding what similar projects used.
- **Google search**: checking current models, prices and free tiers.

## How AI was used

AI was used mainly for **research**: working out which architecture and which
models to use at each step. Every choice was then tested before it was kept.

1. **Understand the task.** After reading the assessment brief, I looked for
   projects similar to this one and noted the structure and architecture they
   used ([Docs/notes/similar-projects.md](Docs/notes/similar-projects.md)).
2. **Plan the steps.** I wrote the basic structure as a list of steps in
   [Docs/notes/plan.md](Docs/notes/plan.md): parsing, chunking, retrieval,
   generation, and so on.
3. **Research each step.** Starting from the first step, I asked ChatGPT,
   Gemini, Claude and a DeepSeek model for the best options, based on similar
   projects and the current best models, and searched Google myself. I then
   checked which options were free or paid and which I could actually use.
4. **Test the options.** For each step I ran experiments to see which option
   gave the best result, and chose based on the numbers. Some of the experiment
   scripts I wrote myself; most were written by Claude Sonnet and checked by me
   before I trusted their results.
5. **Check the combinations.** Every two or three steps, I ran a script again to
   check that the chosen pieces worked well together, or whether another
   combination would be better. For example, which embedder and reranker pair
   is best (E7 and E21 in the [experiment log](Docs/experiments.md)).

Every step of the project was done this way: research with AI, then choose by
measured results.

## How the output was checked

- **My own validation scripts** check each stage against something independent:
  the parsed law against the original PDFs and a separately scraped copy of the
  Acts, the chunks against the parsed law, the database against the local
  files.
- **608 questions with known answers** were used to score every retrieval
  change. A change was kept only if it improved the numbers by more than normal
  variation.
- **Manual testing** of each question type, and a review of the live app by a
  law student from the University of Dhaka.

## AI suggestions that were rejected or corrected

- **Chunk headings.** Published work suggested by AI claimed a heading on each
  chunk cuts search failures by about half. Tested here: no difference (E2).
- **Keyword + vector (hybrid) search.** A common recommendation; results for
  statutes showed no gain, so it was not adopted.
- **Docling as the main PDF parser.** It lost text and broke sections that run
  across pages; kept only for documents that aren't numbered statutes.
- **A small model for sorting questions** (LFM 2.6B). It labelled the same
  question differently between runs, and was replaced by a larger model (E13).
- **Comparing rerankers.** An AI-written test script loaded two rerankers
  incorrectly, making them look far worse than they are. Caught because a
  known model was run alongside as a control; the results were discarded (E6).
- **Two improvements assumed to add up.** They didn't: the second only repeated
  the first one's gain (E7).
- **Gemini for speed.** Suggested for the planner and the memory summary; later
  it became too slow (up to a minute per call) and was replaced (E16, E20).
- **Code written by Claude Code** was corrected when it was over-complicated
  (the setup scripts were cut down), and test runs that used up the free API
  limits were replaced with checks that make no API calls.
