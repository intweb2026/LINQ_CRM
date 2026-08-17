/**
 * Rich text that arrives from Zoho as HTML.
 *
 * Zoho Creator holds the agenda copy in a rich-text field, and its export writes
 * that field as markup, so what the importer stores is
 * `<p><b>TITLE</b><br /></p><ul><li><p>bullet</p></li></ul>` rather than prose.
 * Measured on the development database, 1,893 of the 1,923 proposal submissions
 * are stored that way, and 3,512 of the 3,540 paper reviews the same copy is
 * bridged from. A textarea shows all of it as visible tag soup, which the reader
 * then has to strip in their head to find the three bullet points inside.
 *
 * These helpers render the formatting instead. The markup reaches us from a
 * spreadsheet anyone with the sheet can edit, so it is untrusted; nothing here
 * passes through a tag or an attribute that is not asked for by name, and the
 * parsing goes through DOMParser rather than through a live node's innerHTML, so
 * nothing in the value can run or fetch while it is being inspected.
 */

/**
 * Tags kept as themselves. Formatting only; nothing that loads a resource,
 * nothing that runs. Every other tag is UNWRAPPED rather than deleted, so an
 * unexpected wrapper costs its own tag and never the words inside it.
 */
const ALLOWED = new Set([
  'p', 'br', 'hr', 'div', 'span', 'section', 'article', 'blockquote',
  'b', 'strong', 'i', 'em', 'u', 's', 'strike', 'sub', 'sup', 'mark', 'small',
  'ul', 'ol', 'li', 'dl', 'dt', 'dd',
  'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
  'pre', 'code', 'a',
  'table', 'thead', 'tbody', 'tfoot', 'tr', 'th', 'td', 'caption',
]);

/**
 * Tags removed WITH their contents, the one case where unwrapping is wrong.
 * Unwrapping a script would leave its source behind as visible text, and
 * unwrapping a style would leave the rules; an img is a remote fetch the CRM
 * never asked a spreadsheet to make on its behalf.
 */
const DISCARD = new Set([
  'script', 'style', 'noscript', 'template', 'link', 'meta', 'head', 'title',
  'iframe', 'object', 'embed', 'img', 'svg', 'math', 'canvas', 'audio', 'video',
  'form', 'input', 'select', 'option', 'textarea', 'button',
]);

/**
 * Attributes kept, by tag. `style` is deliberately absent. 388 of these values
 * were pasted out of Word and carry `font-family: "Arial"` and
 * `color: rgb(77, 81, 86)` on every span, which overrides the CRM's own
 * typography and goes unreadable against the dark theme; dropping it hands the
 * spacing and the colour back to the stylesheet, which is where .rt-e sets them.
 */
const KEEP_ATTRS = {
  a: new Set(['href', 'title']),
  td: new Set(['colspan', 'rowspan']),
  th: new Set(['colspan', 'rowspan']),
};

// The schemes a CRM link should ever use, the same shortlist extUrl allows.
const SAFE_HREF = /^(?:https?:|mailto:|tel:)/i;

/**
 * Tag NAMES, not a `<…>` catch-all. A cell reading "<not stated>" is a value
 * somebody typed, and treating it as markup would render it as nothing at all;
 * cellText in lib/helpers.js declines a blanket tag stripper for the same
 * reason. `font` and `o:p` are here because Word writes them and they are worth
 * recognising as markup even though the sanitiser will unwrap them.
 */
const HTML_TAG = new RegExp(
  '</?(?:p|br|hr|div|span|section|article|blockquote|b|strong|i|em|u|s|strike'
  + '|sub|sup|mark|small|ul|ol|li|dl|dt|dd|h[1-6]|pre|code|a|table|thead|tbody'
  + '|tfoot|tr|th|td|caption|font|img|o:p)\\b[^>]*>',
  'i',
);

/** Whether a stored value is markup rather than prose. */
export function looksLikeHtml(v) {
  return HTML_TAG.test(String(v ?? ''));
}

const parse = (html) => new DOMParser().parseFromString(html, 'text/html');

/** Depth-first, children before the parent, so an unwrap cannot skip a node. */
function scrub(parent) {
  for (const node of Array.from(parent.childNodes)) {
    if (node.nodeType === 3) continue;                 // text, kept verbatim
    if (node.nodeType !== 1) { node.remove(); continue; }  // comments, CDATA
    const tag = node.tagName.toLowerCase();
    if (DISCARD.has(tag)) { node.remove(); continue; }
    scrub(node);
    if (!ALLOWED.has(tag)) { node.replaceWith(...node.childNodes); continue; }
    const keep = KEEP_ATTRS[tag];
    for (const name of node.getAttributeNames()) {
      if (!keep || !keep.has(name)) node.removeAttribute(name);
    }
    if (tag === 'a') {
      const href = (node.getAttribute('href') || '').trim();
      if (SAFE_HREF.test(href)) {
        node.setAttribute('href', href);
        // Its own tab, with no handle back to this one, and no vote of
        // confidence in an address that came out of a spreadsheet.
        node.setAttribute('target', '_blank');
        node.setAttribute('rel', 'noopener noreferrer nofollow');
      } else {
        // A scheme-less href is a path relative to the CRM's own origin, so
        // clicking it would reload the app; the words stay, the link goes.
        node.removeAttribute('href');
      }
    }
  }
}

/** A stored value reduced to the markup this app is willing to render. */
export function sanitizeHtml(v) {
  const s = String(v ?? '');
  if (!s) return '';
  const doc = parse(s);
  scrub(doc.body);
  return doc.body.innerHTML;
}

// Anything that ends a line, for the text reduction below.
const BLOCKS = 'p,div,br,hr,li,ul,ol,tr,caption,dt,dd,h1,h2,h3,h4,h5,h6,'
  + 'blockquote,pre,section,article';

/**
 * A stored value as one line of readable prose, for a table cell that has room
 * for a single line and an ellipsis. Markup only; a value with no tags in it
 * comes back untouched, on the same reasoning as looksLikeHtml.
 */
export function htmlToText(v) {
  const s = String(v ?? '');
  if (!looksLikeHtml(s)) return s;
  const doc = parse(s);
  doc.body.querySelectorAll('script,style').forEach((n) => n.remove());
  // Without a separator at every block boundary, `</p><p>` joins the last word
  // of one paragraph to the first word of the next.
  doc.body.querySelectorAll(BLOCKS).forEach((n) => n.after(doc.createTextNode(' ')));
  return (doc.body.textContent || '').replace(/\s+/g, ' ').trim();
}

/**
 * What the editable node holds, turned into what the field should store.
 *
 * Two normalisations, both of which the raw innerHTML gets wrong. A cleared
 * field must store the empty string rather than the `<br>` or `<div><br></div>`
 * a browser leaves behind, or the list would stop showing a dash and the export
 * would carry a row of markup where the user left nothing. And text typed with
 * no formatting comes out with no tags in it, which the reader above cannot tell
 * from a legacy plain-text row; an `&` in such a value would be escaped once on
 * the way to the store and escaped again on the way back to the screen, so one
 * wrapping paragraph makes the stored value unambiguously markup.
 */
export function normalizeEditorHtml(rawHtml) {
  const clean = sanitizeHtml(rawHtml);
  if (!clean) return '';
  const doc = parse(clean);
  const hasWords = (doc.body.textContent || '').trim() !== '';
  const hasMarks = doc.body.querySelector('hr,table') != null;
  if (!hasWords && !hasMarks) return '';
  return looksLikeHtml(clean) ? clean : '<p>' + clean + '</p>';
}

/**
 * Put a stored value on screen inside `el`.
 *
 * The plain-text branch writes text nodes and br elements rather than escaping
 * the string into HTML, so a legacy value holding `&` or `<` is shown as itself
 * and cannot be escaped a second time on a later pass. It also means the field
 * never depends on white-space:pre-wrap to show the line breaks in the handful
 * of rows that were typed into the old textarea, which matters because 388 of
 * the markup rows carry Word's soft line breaks INSIDE their sentences, where a
 * newline has to collapse to a space to read correctly.
 */
export function renderInto(el, value) {
  const s = String(value ?? '');
  if (looksLikeHtml(s)) {
    el.innerHTML = sanitizeHtml(s);
    return;
  }
  el.textContent = '';
  const doc = el.ownerDocument;
  s.split(/\r\n|\r|\n/).forEach((line, i) => {
    if (i) el.appendChild(doc.createElement('br'));
    el.appendChild(doc.createTextNode(line));
  });
}
