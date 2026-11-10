/** Characters at the end of a file name that stay visible when the name is cut: the
 *  extension and the last characters are what tell similar files apart. */
const TAIL = 10;
/** A long extension stays whole, up to this many characters. */
const LONGEST_TAIL = 16;

function graphemes(text: string): string[] {
  if (typeof Intl.Segmenter !== 'function') return Array.from(text);
  const segmenter = new Intl.Segmenter(undefined, { granularity: 'grapheme' });
  return Array.from(segmenter.segment(text), (part) => part.segment);
}

/**
 * Splits a name into a head that may shrink behind an ellipsis and a tail that never does.
 * The split falls between user-perceived characters, so an emoji or an accented letter is
 * never torn apart, and the two parts keep their logical order (right-to-left text included).
 */
export function splitFileName(name: string): [string, string] {
  const parts = graphemes(name);
  if (parts.length <= TAIL) return [name, ''];
  const dot = parts.lastIndexOf('.');
  const extension = dot > 0 ? parts.length - dot : 0;
  const tail = extension > TAIL && extension <= LONGEST_TAIL ? extension : TAIL;
  return [parts.slice(0, -tail).join(''), parts.slice(-tail).join('')];
}
