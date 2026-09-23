import Image from "next/image";

/**
 * The AinShonglap logo, in the two forms the interface needs.
 *
 *   mark   আ inside the scales of justice. Used wherever it is small — the
 *          header, and the avatar beside every answer. At 32px the wordmark
 *          under it would be unreadable, so small sizes show the mark alone.
 *   full   mark + আইনসংলাপ. Only where there is room: the welcome and sign-in
 *          pages.
 *
 * Both are cropped from `public/logo.jpeg` into `public/brand/`. The original is
 * 1.2 MB at 2110×2048; the mark is 35 KB and the full logo 123 KB, and
 * `next/image` serves each at the size it is shown. The logo's own background
 * is the #1B1B1B it was drawn on, so the rounded tile uses that colour rather
 * than a slate that would sit a shade off beside it.
 */
interface BrandMarkProps {
  /** Rendered size in px. */
  size: number;
  variant?: "mark" | "full";
  /** Rounded tile, as used for avatars and page headers. */
  className?: string;
  /** Above the fold: load it without waiting (replaces the deprecated `priority`). */
  eager?: boolean;
}

export function BrandMark({ size, variant = "mark", className = "", eager = false }: BrandMarkProps) {
  const src = variant === "full" ? "/brand/logo.png" : "/brand/mark.png";
  return (
    <Image
      src={src}
      alt="আইনসংলাপ"
      width={size}
      height={size}
      loading={eager ? "eager" : "lazy"}
      className={`flex-shrink-0 bg-[#1b1b1b] ${className}`}
    />
  );
}
