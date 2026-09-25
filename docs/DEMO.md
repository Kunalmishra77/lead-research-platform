Written for: you, presenting this to someone who has not seen it before.

# Demo script, start to finish

## What you are actually showing

A research engine that turns a sentence into real businesses, and can prove where every single
value came from. The provenance is the thing worth showing — plenty of tools produce a list of
leads; few can tell you which source said what, when, and how sure they are.

Be straight about the stage. This is the discovery half of the product, working end to end. The
crawling, enrichment, scoring and export halves are not built. If you show it as a finished
product, the first question will find the edge; if you show it as a working engine with the rest
mapped out, the same question becomes a roadmap conversation.

## Before anyone is watching

1. **Have a finished job ready.** Run one search the night before, or an hour before. A live run
   takes a few minutes, and a demo that opens on a completed job with 100 leads lands better than
   one that opens on an empty table.
2. **Check the four pieces are up** (`redis`, `workers`, `api`, `web`). If the workers are down,
   a new search will sit at "planning" for ever and you will not know why in the room.
3. **Check the credit balance.** The free plan grants 50 credits and a standard lead costs 3, so
   one demo account runs out after about 16 leads. Use an account with room, or run the live
   search at `quick` depth (1 credit a lead).
4. **Decide whether you will run a live search.** It costs about $0.035 per search — a Delhi run
   was $0.60 for 106 leads. It is impressive when it works and awkward when the network is slow.
   Having a finished job to fall back on means you can start it and move on.

---

## The demo, in five minutes

### 1. Start with the sentence (30 seconds)

Open **New Research**. Type it out rather than pasting, so they see it is free text:

> `dental clinics in Delhi with a website`

Press **Understand this**.

Say: *"Nothing has been charged yet. It is reading the request first, and it is going to show me
what it understood before I spend anything."*

### 2. The spec is the point (1 minute)

The chips appear: Industry, Location, Columns. Each one carries how far we can take it —
**searchable**, **checked later**, **estimated**.

This is the part people miss, so say it out loud:

> *"It is telling me the difference between a filter it can actually search for and one it can
> only check after collecting the data. Most tools quietly apply both the same way and you never
> find out which number was measured and which was guessed."*

Remove a chip. Watch the credit estimate on the Run button change. That is the whole "cost before
commit" idea in one gesture.

### 3. Depth and cost (30 seconds)

Click through Quick / Standard / Deep. The button updates: *Run · N credits*.

> *"It holds the credits, runs, and gives back whatever it did not use."*

### 4. Run it — or open the one you prepared (2 minutes)

If running live: press Run. The job page opens and the counters move as tasks finish —
businesses seen, leads delivered, values stored, credits used. Let it run while you talk.

If you prepared one: open it from **Research** in the sidebar.

While it runs, this is the best moment for the architecture line:

> *"Each of those is a separate search running against Google's Places API — a different phrasing,
> or a different part of the city. It plans them, it funds each one out of the job's budget, and
> if one runs out of money it keeps whatever it already paid for instead of throwing it away."*

### 5. The leads, and the proof (1 minute)

Scroll to the table. Then do the thing nobody else does — point at a dot.

> *"Every value carries where it came from. A filled dot means a source stated it. A hollow one
> means a rule of ours worked it out. A sparkle means a model inferred it. Hover any of them and
> it tells you the source and the date."*

Point at the line underneath: **Business data from Google · Data © OpenStreetMap contributors**.

> *"That is not decoration. Google's terms require the attribution wherever their content is
> shown, and the geography is OpenStreetMap under ODbL. The page renders it from the sources the
> data actually came from, so it cannot drift out of date."*

---

## The questions you will get, and honest answers

**"Can I export this to CSV / Sheets?"**
Not yet — that is the next block of work (Phase 7, with billing). Today the leads live in the
database with full provenance; getting them out is a build, not a research problem.

**"Where are the email addresses?"**
Discovery gets what a maps listing carries: name, address, phone, website, rating, hours. Emails
come from crawling the company's website, which is the next phase. The website is already there
for about half of them, which is what makes that crawl possible.

**"Can I search or filter the leads I already have?"**
Not yet. The results grid, lists and tags are Phase 4. Right now you see a job's results.

**"How accurate is it?"**
Better answer than a number: *"Every value says where it came from and how confident we are, so
you can check any of them. We would rather show you that than a single accuracy figure that
hides which parts were guessed."* If they press: Places data is stated by Google at 0.85
confidence; a website we picked by matching the business name to the domain is marked as derived,
not found, and is deliberately scored lower.

**"What happens if two of us search the same market?"**
You each get the full list and each pay for your own leads. The underlying business record is
shared, so we do not pay Google twice — but that saving is ours, not a reason to give you half an
answer. *(This was a real bug, found and fixed: the second customer used to receive nothing.)*

**"Is it fast?"**
A city takes a few minutes, because it is doing tens of real searches and covering the city in
pieces. It is not a database lookup; it is going and finding out.

**"What does it cost to run?"**
About 60 cents of Google Places for ~100 businesses in a city, at our current settings. That is
the number the credit system is built on.

---

## If something goes wrong in the room

**The job sits at "planning" and nothing happens.** The workers are not running. Do not debug it
live — switch to the finished job you prepared and say the background workers are restarting.

**"No source can find businesses for this request."** `GOOGLE_PLACES_ENABLED` is off. Same move:
switch to the prepared job.

**The live counters stop moving.** The progress stream has a ten-minute limit and simply ends.
Reload the page — the job keeps running regardless, and the page reads the real state from the
database when it loads.

**A search returns fewer leads than you expected.** The job's budget bounds how many searches it
can fund. Say so plainly: *"It stopped because it hit the budget I gave it, which is the
behaviour I want — it will not quietly spend more than I told it to."*

---

## What to promise

Safe to say today: it finds real businesses from a plain-English request, it shows where every
value came from, and it will not spend more than the budget it was given.

Do not promise dates for export, emails or scoring unless you have decided them yourself. The
roadmap has the work; what it does not have is a delivery date you have committed to.
