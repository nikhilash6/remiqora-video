// Light markdown for the writer's thinking (components/WriterThinking.vue): paragraphs, numbered and bulleted lists
// (nested by indent), headings as bold lines, **bold**, *italic*, `code`. Parsed into plain data and rendered by the
// template as text nodes - never v-html, the model's text is not trusted markup. A text still streaming may end
// inside an unclosed ** or `: the open marker is dropped, not shown.

export type Inline = { k: 't' | 'b' | 'i' | 'c'; v: string }
export type Block =
  | { k: 'p'; inl: Inline[] }
  | { k: 'h'; inl: Inline[] }
  | { k: 'li'; mark: string; ordered: boolean; depth: number; inl: Inline[] }

const LI = /^(\s*)([-*+•]|\d{1,3}[.)])\s+(.*)$/
const HEAD = /^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$/
const RULE = /^\s*([-*_])(\s*\1){2,}\s*$/

export function inline(s: string): Inline[] {
  const out: Inline[] = []
  const push = (k: Inline['k'], v: string) => {
    if (!v) return
    const last = out[out.length - 1]
    if (last && last.k === k) last.v += v
    else out.push({ k, v })
  }
  let i = 0
  while (i < s.length) {
    const rest = s.slice(i)
    let m: RegExpMatchArray | null
    if ((m = rest.match(/^`([^`]+)`/))) { push('c', m[1]); i += m[0].length; continue }
    if ((m = rest.match(/^\*\*(?=\S)([\s\S]*?\S)\*\*/)) || (m = rest.match(/^__(?=\S)([\s\S]*?\S)__/))) {
      // bold may hold italic: flattened to bold, the inner markers dropped
      push('b', m[1].replace(/(^|[^*])\*(?!\*)|_(?!_)/g, '$1')); i += m[0].length; continue
    }
    if ((m = rest.match(/^\*(?=[^\s*])([^*\n]*?[^\s*])\*(?!\*)/)) || (m = rest.match(/^_(?=\S)([^_\n]*?\S)_(?![\p{L}\p{N}])/u))) {
      if (m[0][0] === '_' && i > 0 && /[\p{L}\p{N}]/u.test(s[i - 1])) { push('t', '_'); i += 1; continue }   // snake_case
      push('i', m[1]); i += m[0].length; continue
    }
    // an unclosed marker (the stream stopped mid-word) or a stray one: not shown
    if ((m = rest.match(/^(\*\*|__|`)/)) && !rest.slice(m[0].length).includes(m[0])) { i += m[0].length; continue }
    push('t', s[i]); i += 1
  }
  return out
}

export function parse(text: string): Block[] {
  const out: Block[] = []
  let para: string[] = []
  const flush = () => {
    if (para.length) out.push({ k: 'p', inl: inline(para.join(' ')) })
    para = []
  }
  // the indent of the list's first level, so a list indented as a whole is not «nested»
  let base = -1
  for (const raw of text.replace(/\r/g, '').split('\n')) {
    const line = raw.replace(/\t/g, '    ')
    if (!line.trim() || RULE.test(line)) { flush(); if (!line.trim()) base = -1; continue }
    let m: RegExpMatchArray | null
    if ((m = line.match(HEAD))) { flush(); out.push({ k: 'h', inl: inline(m[1]) }); base = -1; continue }
    if ((m = line.match(LI))) {
      flush()
      const ind = m[1].length
      if (base < 0 || ind < base) base = ind
      const ordered = /\d/.test(m[2])
      out.push({ k: 'li', mark: ordered ? m[2].replace(')', '.') : '•', ordered, depth: Math.min(3, Math.floor((ind - base) / 2)), inl: inline(m[3]) })
      continue
    }
    const last = out[out.length - 1]
    if (!para.length && last && last.k === 'li' && /^\s+/.test(line)) {   // a list item's own next line
      last.inl.push(...inline(' ' + line.trim()))
      continue
    }
    para.push(line.trim())
  }
  flush()
  return out
}
