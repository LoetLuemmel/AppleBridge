/* html2text — a streaming HTML-to-text reducer for a 32 KB TextEdit window.
 * Mirrors the WebPeek gateway's rules: script/style/head dropped (title kept),
 * block tags become line breaks, entities decoded, UTF-8 folded to MacRoman,
 * whitespace collapsed. Anchors are collected (href + visible text) so the
 * application can offer them as a menu. Feed arbitrary chunks; the state
 * machine copes with tags split across chunks. Output is CR-separated text. */
#ifndef HTML2TEXT_H
#define HTML2TEXT_H

#define H2T_MAX_LINKS 24
#define H2T_LINK_LEN  200

typedef struct {
    char *out; long cap, len;         /* text buffer (CR line ends) */
    char title[120];
    char links[H2T_MAX_LINKS][H2T_LINK_LEN];
    char link_text[H2T_MAX_LINKS][40];
    int  nlinks;
    /* state */
    int  in_tag, in_skip, in_title, in_comment, pending_nl, last_space, saw_text;
    char tag[65]; int taglen;
    int  tag_is_close, in_attr_href, quote; char href[H2T_LINK_LEN]; int hreflen;
    int  utf_pending, utf_need; unsigned long utf_cp;
    int  in_entity; char ent[12]; int entlen;
    int  cur_link;                    /* index while inside <a>, else -1 */
    int  link_text_len;
    int  full;                        /* output cap reached */
} h2t_state;

void h2t_init(h2t_state *s, char *out, long cap);
void h2t_feed(h2t_state *s, const unsigned char *data, long len);
void h2t_finish(h2t_state *s);

/* Resolve `href` against `base_url` into `out` (cap n): absolute, scheme-relative,
 * root-relative and relative paths. 1 ok, 0 unusable (mailto:, javascript:, #). */
int h2t_resolve(const char *base_url, const char *href, char *out, int n);

#endif
