/* html2text.c — see html2text.h */
#include <string.h>
#include <stdlib.h>
#include <ctype.h>
#include "html2text.h"
#include "macroman_table.h"

static const char *BLOCK[] = { "p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol",
                               "table", "blockquote", "pre", "hr", "section", "article", "header", "footer", "nav", "dt", "dd", 0 };
static const char *SKIP[]  = { "script", "style", "head", "noscript", "svg", 0 };

static int in_list(const char **l, const char *t) { while (*l) { if (!strcmp(*l, t)) return 1; l++; } return 0; }

void h2t_init(h2t_state *s, char *out, long cap) {
    memset(s, 0, sizeof *s); s->out = out; s->cap = cap; s->cur_link = -1; s->last_space = 1;
}

static void emit_byte(h2t_state *s, unsigned char c) {
    if (s->in_title) { size_t n = strlen(s->title); if (n < sizeof s->title - 1 && !(c == ' ' && (n == 0 || s->title[n-1] == ' '))) { s->title[n] = c; s->title[n+1] = 0; } return; }
    if (s->in_skip) return;
    if (s->cur_link >= 0 && s->link_text_len < 39 && c != '\r') { s->link_text[s->cur_link][s->link_text_len++] = c; s->link_text[s->cur_link][s->link_text_len] = 0; }
    if (s->len >= s->cap - 1) { s->full = 1; return; }
    if (c == '\r') {                                   /* block break: at most one blank line */
        if (s->pending_nl >= 2) return;
        s->pending_nl++; s->last_space = 1; return;
    }
    if (c == ' ') { if (s->last_space) return; s->last_space = 1; }
    else s->last_space = 0;
    while (s->pending_nl > 0 && s->len < s->cap - 1) { if (s->saw_text) s->out[s->len++] = '\r'; s->pending_nl--; }
    s->pending_nl = 0;
    s->out[s->len++] = c; s->saw_text = 1;
}

static void emit_cp(h2t_state *s, unsigned long cp) {
    unsigned i;
    if (cp == '\n' || cp == '\t' || cp == '\r' || cp == ' ' || cp == 0xA0) { emit_byte(s, ' '); return; }
    if (cp < 0x80) { emit_byte(s, (unsigned char)cp); return; }
    if (cp >= 0xA0 && cp <= 0xFF) { emit_byte(s, latin1_to_macroman[cp - 0xA0]); return; }
    for (i = 0; i < sizeof punct_to_macroman / sizeof punct_to_macroman[0]; i++)
        if (punct_to_macroman[i].cp == cp) { emit_byte(s, punct_to_macroman[i].mac); return; }
    emit_byte(s, '?');
}

static void end_entity(h2t_state *s) {
    unsigned long cp = 0; const char *e = s->ent;
    s->in_entity = 0;
    if (e[0] == '#') cp = (e[1] == 'x' || e[1] == 'X') ? strtoul(e + 2, 0, 16) : strtoul(e + 1, 0, 10);
    else if (!strcmp(e, "amp")) cp = '&'; else if (!strcmp(e, "lt")) cp = '<'; else if (!strcmp(e, "gt")) cp = '>';
    else if (!strcmp(e, "quot")) cp = '"'; else if (!strcmp(e, "apos")) cp = '\''; else if (!strcmp(e, "nbsp")) cp = ' ';
    else if (!strcmp(e, "mdash")) cp = 0x2014; else if (!strcmp(e, "ndash")) cp = 0x2013; else if (!strcmp(e, "hellip")) cp = 0x2026;
    else if (!strcmp(e, "copy")) cp = 0xA9; else if (!strcmp(e, "reg")) cp = 0xAE; else if (!strcmp(e, "laquo")) cp = 0xAB; else if (!strcmp(e, "raquo")) cp = 0xBB;
    else if (!strcmp(e, "auml")) cp = 0xE4; else if (!strcmp(e, "ouml")) cp = 0xF6; else if (!strcmp(e, "uuml")) cp = 0xFC; else if (!strcmp(e, "szlig")) cp = 0xDF;
    else if (!strcmp(e, "Auml")) cp = 0xC4; else if (!strcmp(e, "Ouml")) cp = 0xD6; else if (!strcmp(e, "Uuml")) cp = 0xDC;
    else if (!strcmp(e, "rsquo")) cp = 0x2019; else if (!strcmp(e, "lsquo")) cp = 0x2018; else if (!strcmp(e, "rdquo")) cp = 0x201D; else if (!strcmp(e, "ldquo")) cp = 0x201C;
    if (cp) emit_cp(s, cp); else { emit_byte(s, '&'); { const char *q = s->ent; while (*q) emit_byte(s, *q++); } }
    s->entlen = 0; s->ent[0] = 0;
}

static void text_byte(h2t_state *s, unsigned char c) {
    if (s->in_entity) {
        if (c == ';') { end_entity(s); return; }
        if ((isalnum(c) || c == '#') && s->entlen < 10) { s->ent[s->entlen++] = c; s->ent[s->entlen] = 0; return; }
        end_entity(s);                                       /* malformed: flush what we had */
    }
    if (c == '&') { s->in_entity = 1; s->entlen = 0; s->ent[0] = 0; return; }
    if (s->utf_pending) {
        if ((c & 0xC0) == 0x80) { s->utf_cp = (s->utf_cp << 6) | (c & 0x3F); if (--s->utf_need == 0) { s->utf_pending = 0; emit_cp(s, s->utf_cp); } return; }
        s->utf_pending = 0;                                  /* broken sequence */
    }
    if (c >= 0xC0) {
        if ((c & 0xE0) == 0xC0) { s->utf_cp = c & 0x1F; s->utf_need = 1; }
        else if ((c & 0xF0) == 0xE0) { s->utf_cp = c & 0x0F; s->utf_need = 2; }
        else if ((c & 0xF8) == 0xF0) { s->utf_cp = c & 0x07; s->utf_need = 3; }
        else { emit_byte(s, '?'); return; }
        s->utf_pending = 1; return;
    }
    if (c >= 0x80) { emit_byte(s, '?'); return; }
    emit_cp(s, c);
}

static void end_tag(h2t_state *s) {
    char name[64]; int i = 0; const char *t = s->tag;
    s->in_tag = 0;
    if (s->taglen == 0) return;
    s->tag[s->taglen] = 0;                    /* the buffer is reused: terminate, or a short tag inherits the last long one's tail */
    while (*t && !isspace((unsigned char)*t) && *t != '/' && i < 63) name[i++] = tolower((unsigned char)*t++);
    name[i] = 0;
    if (!strcmp(name, "title")) { s->in_title = !s->tag_is_close; return; }
    if (in_list(SKIP, name)) { if (s->tag_is_close) { if (s->in_skip) s->in_skip--; } else s->in_skip++; return; }
    if (in_list(BLOCK, name)) { emit_byte(s, '\r'); if (!s->tag_is_close && (name[0] == 'h' && name[1] >= '1' && name[1] <= '6')) emit_byte(s, '\r'); }
    if (!strcmp(name, "a")) {
        if (!s->tag_is_close) {
            if (s->hreflen > 0 && s->nlinks < H2T_MAX_LINKS && !s->in_skip) {
                s->href[s->hreflen] = 0;
                if (strncmp(s->href, "javascript:", 11) && strncmp(s->href, "mailto:", 7) && s->href[0] != '#') {
                    strcpy(s->links[s->nlinks], s->href); s->link_text[s->nlinks][0] = 0;
                    s->cur_link = s->nlinks++; s->link_text_len = 0;
                }
            }
        } else s->cur_link = -1;
    }
    if (!strcmp(name, "td") || !strcmp(name, "th")) emit_byte(s, ' ');
    if (!strcmp(name, "img")) emit_byte(s, ' ');
}

void h2t_feed(h2t_state *s, const unsigned char *data, long len) {
    long i;
    for (i = 0; i < len; i++) {
        unsigned char c = data[i];
        if (s->in_comment) { if (c == '>' && s->taglen >= 2 && s->tag[s->taglen-1] == '-' && s->tag[s->taglen-2] == '-') { s->in_comment = 0; s->in_tag = 0; s->taglen = 0; } else { s->tag[0] = s->tag[1]; s->tag[1] = c; s->taglen = 2; } continue; }
        if (s->in_tag) {
            if (c == '>' && !s->quote) { end_tag(s); s->taglen = 0; s->in_attr_href = 0; s->hreflen = 0; continue; }
            if (s->taglen == 3 && s->tag[0] == '!' && s->tag[1] == '-' && s->tag[2] == '-') { s->in_comment = 1; s->tag[0] = s->tag[1] = 0; s->taglen = 2; continue; }
            if (s->taglen < 63) s->tag[s->taglen++] = c;
            /* href="..." capture */
            if (s->in_attr_href) {
                if (s->quote) { if (c == s->quote) s->in_attr_href = 0; else if (s->hreflen < H2T_LINK_LEN - 1) s->href[s->hreflen++] = c; }
                else if (c == '"' || c == '\'') s->quote = c;
                else if (isspace(c)) s->in_attr_href = 0;
                else if (s->hreflen < H2T_LINK_LEN - 1) s->href[s->hreflen++] = c;
                if (!s->in_attr_href) s->quote = 0;
                continue;
            }
            if (s->quote) { if (c == s->quote) s->quote = 0; continue; }
            if (c == '"' || c == '\'') { s->quote = c; continue; }
            if (c == '=' && s->taglen >= 5 && !strncasecmp(s->tag + s->taglen - 5, "href=", 5) && (s->taglen == 5 || isspace((unsigned char)s->tag[s->taglen - 6]))) { s->in_attr_href = 1; s->hreflen = 0; s->quote = 0; }
            continue;
        }
        if (c == '<') { s->in_tag = 1; s->taglen = 0; s->tag_is_close = 0; s->quote = 0; if (i + 1 < len && data[i+1] == '/') { s->tag_is_close = 1; i++; } continue; }
        text_byte(s, c);
    }
}

void h2t_finish(h2t_state *s) { if (s->in_entity) end_entity(s); if (s->len < s->cap) s->out[s->len] = 0; }

int h2t_resolve(const char *base, const char *href, char *out, int n) {
    const char *p, *path; char scheme_host[400]; int i;
    if (!strncmp(href, "http://", 7) || !strncmp(href, "https://", 8)) { strncpy(out, href, n - 1); out[n-1] = 0; return 1; }
    if (!strncmp(href, "mailto:", 7) || !strncmp(href, "javascript:", 11) || href[0] == '#' || !href[0]) return 0;
    p = strstr(base, "://"); if (!p) return 0; p += 3;
    path = strchr(p, '/');
    i = path ? (int)(path - base) : (int)strlen(base);
    if (i >= (int)sizeof scheme_host) return 0;
    memcpy(scheme_host, base, i); scheme_host[i] = 0;
    if (href[0] == '/' && href[1] == '/') { const char *s = strchr(base, ':'); int k = (int)(s - base); memcpy(out, base, k); out[k] = ':'; strncpy(out + k + 1, href, n - k - 2); out[n-1] = 0; return 1; }
    if (href[0] == '/') { strcpy(out, scheme_host); strncat(out, href, n - strlen(out) - 1); return 1; }
    /* relative: base directory */
    { const char *last = path ? strrchr(path, '/') : NULL; int k = last ? (int)(last - base) + 1 : i;
      if (k + (int)strlen(href) + 2 > n) return 0;
      memcpy(out, base, k); if (!path) out[k++] = '/'; strcpy(out + k, href); }
    return 1;
}
