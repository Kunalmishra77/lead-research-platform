"""Crawling a lead's own website (Phase 3, docs/06 section 4).

Everything here obeys one rule from CLAUDE.md: we never evade a block. robots.txt disallow,
challenge pages, logins and paywalls all end the same way -- the target is marked
`access_restricted` and nothing retries or works around it.
"""
