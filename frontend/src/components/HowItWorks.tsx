// M17 Decision 5: "How it works", below the start screen's two columns, reached from the
// header's link. Start screen only, never during a defense. Every claim here is listed
// with its evidence in docs/v2.0-m17-results.md; one that loses its evidence comes out.
import type { ReactNode } from 'react'

export const HOW_IT_WORKS_ID = 'how-it-works'
export const SOURCE_URL = 'https://github.com/slvr123/Academic-Defense-Simulator'

const BLOCKS: { title: string; body: ReactNode }[] = [
  {
    title: 'RAG, turned around.',
    body: (
      <>
        Instead of searching your paper to answer your questions, the panel searches it to question <em>you</em>. Each panelist looks for something worth
        challenging (a claim, a number, a decision) and asks about it. After you answer, you see the exact passage and page it
        came from.
      </>
    ),
  },
  {
    title: 'A panel, not a chatbot.',
    body: (
      <>
        Each panelist has a lane, such as problem and objectives, methodology, or results. Each opens in its own chapter when your PDF's
        chapter headings can be read, and with the preset panels the first round walks forward through your paper. A Terror Panelist speaks last
        and goes after your earlier answers.
      </>
    ),
  },
  {
    title: 'It adapts, quietly.',
    body: (
      <>
        Every answer is scored against a rubric you don't see, and weak answers draw follow-ups. Scores appear only in the
        debrief, with suggestions grounded in the same passages.
      </>
    ),
  },
  {
    title: 'Measured, not guessed.',
    body: (
      <>
        We test the panel against real papers and change it when the results say so. When the faster model misjudged weak
        answers, scoring moved to a stronger one. When panelists quizzed students on other authors' studies as if the work
        were theirs, we measured it, had it independently judged, and fixed it. Opening rounds that walk forward through the
        paper went from 5 of 10 to 10 of 10 on our eval. And when a scoring result didn't hold up on a second paper, we said
        so instead of quietly dropping it.
      </>
    ),
  },
]

export function HowItWorks() {
  return (
    <section id={HOW_IT_WORKS_ID} aria-labelledby={`${HOW_IT_WORKS_ID}-title`} className="scroll-mt-4 border-t border-line-strong pt-8">
      <h2 id={`${HOW_IT_WORKS_ID}-title`} className="font-serif text-[28px] leading-tight font-semibold tracking-[-0.01em]">
        How it works
      </h2>
      <div className="mt-5 grid gap-x-10 gap-y-6 md:grid-cols-2">
        {BLOCKS.map((block) => (
          <p key={block.title} className="max-w-[60ch] leading-relaxed text-muted">
            <strong className="font-semibold text-ink">{block.title}</strong> {block.body}
          </p>
        ))}
      </div>
      <p className="mt-6 text-sm text-subtle">
        Built by Sean Silver Allata with FastAPI, React and Gemini, with retrieval in plain numpy.{' '}
        <a href={SOURCE_URL} target="_blank" rel="noopener noreferrer" className="text-action-text underline underline-offset-[3px]">
          Source on GitHub
        </a>
      </p>
    </section>
  )
}

/** The header's link: scrolls to the section without putting anything in the URL. */
export function HowItWorksLink() {
  return (
    <a
      href={`#${HOW_IT_WORKS_ID}`}
      onClick={(event) => {
        const target = document.getElementById(HOW_IT_WORKS_ID)
        if (!target) return
        event.preventDefault()
        const smooth = !window.matchMedia('(prefers-reduced-motion: reduce)').matches
        target.scrollIntoView({ behavior: smooth ? 'smooth' : 'auto', block: 'start' })
      }}
      className="inline-flex min-h-11 items-center text-sm text-action-text underline-offset-[3px] hover:underline"
    >
      How it works
    </a>
  )
}
