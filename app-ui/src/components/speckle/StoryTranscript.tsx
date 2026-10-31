"use client";

/**
 * The Earth chapters in reading order — for assistive technology.
 *
 * The visual experience of chapters I–III is a pinned, scroll-driven stage;
 * this transcript gives screen readers the same content as a linear
 * document. Chapters IV–VI are real, flowing DOM sections, so they read
 * naturally and need no transcript.
 */

import { HERO, CHAPTER_2, CHAPTER_3 } from "./data";

export default function StoryTranscript() {
  return (
    <article className="sr-only">
      <h1>
        {HERO.title}: {HERO.subtitle}
      </h1>
      <p>{HERO.eyebrow}</p>
      <p>{HERO.body}</p>

      <h2>{CHAPTER_2.kicker}</h2>
      <p>{CHAPTER_2.heading}</p>
      <p>{CHAPTER_2.body}</p>

      <h2>{CHAPTER_3.kicker}</h2>
      <p>{CHAPTER_3.heading}</p>
      <p>{CHAPTER_3.body}</p>
    </article>
  );
}
