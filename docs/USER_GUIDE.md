# LGV Manager – User guide

## In brief

LGV Manager is the fund's app for tracking **deals** (the pipeline) and **portfolio companies**: rounds, cap tables, investors, documents, events and the value of our stakes.

- **Where:** in a browser at <https://leo.internet-box.ch>, on a computer, phone or iPad. Nothing to install.
- **What you need:** a user account created by the administrator, and an authenticator app on your phone (Microsoft or Google Authenticator).
- **Conventions:** button and card names are in **bold**, as they appear on screen.
- **Claude:** whatever Claude finds on the web or reads in an email arrives as a **suggestion**. It becomes data only when someone clicks **Accept**.

## 1. First login

On your first login the app asks you to set up two-factor authentication; you cannot get in without it.

1. Open the app and sign in with the user name and password you received from the administrator.
2. On your phone, open the authenticator app and add an account by scanning the **QR code** on screen. In Microsoft Authenticator choose **Other account**.
3. Type the 6-digit code in the **Token** field and click **Next**.
4. Go to **Security** and generate your **backup tokens**. Keep them outside the computer, for example in your password manager.

From then on you need your user name, password and the current code. If you change or lose your phone, sign in with a backup token or ask the administrator to reset your two-factor authentication.

## 2. Finding your way

The menu at the top right is the same on every page.

| Menu | What you find there |
| --- | --- |
| **Home** | What needs attention: deadlines in the next 14 days (overdue in red), runway alerts, upcoming general assemblies, recent financing rounds, recent activity |
| **Pipeline** | All deals in columns by stage, from Sourced to Invested; On hold and Passed are folded below |
| **Portfolio** | Our portfolio companies with stake, invested amount, value, multiple, IRR, runway, last report and next general assembly, with totals |
| **Documents** | Every document of every company: search inside the files, filter by company, category, folder and year |
| **Inbox** | Emails sent to the app and what was done with them; upload of saved emails |
| **Data** | Every table with every field, for rare cases and corrections |
| **Manual** | This guide |
| **Security** | Your two-factor authentication and backup tokens |

Dates are always written **YYYY-MM-DD** (for example 2026-11-20), in the pages and when you type them.

Most cards on a page have an **Edit** button: the card turns into a small form, you change the values and click **Save**, without leaving the page.

## 3. Adding a company and a deal

A new company enters the app together with its first deal. There are three ways in: this form, an email to the app (section 6), or a company page (**New deal** for a further round).

1. Go to **Pipeline** (or **Home**) and click **New deal**.
2. Choose an **Existing company**, or type the name of a new one in **…or new company** (and its sector).
3. Fill in what you know: round, round size, pre-money valuation, our ticket, currency, round deadline, source contact. Everything can be completed later.
4. Click **Create**: the deal page opens, and the deal appears in **Pipeline** under *Sourced*.
5. To have Claude look for the company's description, past rounds, investors, people and news, click **Search the web** at the bottom of the page (section 5).

## 4. Working on a deal

The deal page holds everything needed to decide on a round; open it from **Pipeline** by clicking its card.

| Card | What it shows and how to use it |
| --- | --- |
| **Stage** (top right) | Move the deal through Sourced, Qualified, Review, Due diligence, Term sheet, Invested, or On hold / Passed. Every change is logged with its date. |
| **Round** | Round size, pre-money, post-money (calculated), deadline with countdown, our ticket and the stake it implies. **Edit** to change. |
| **About** the company | Description, sector, location, website, last financing round; **Company page** opens the full summary. |
| **Next step** | What to do next and by when; overdue steps turn red here and on **Home**. |
| **Investors in the round** | Each investor with type (VC, strategic, angel…), role (lead, participant) and status (contacted → signed); the bar shows how much of the round is committed. Type a name and click **Add investor**. Existing investors of the company not yet in the round appear below, with **+ Add to this round**. |
| **Funding history** | Past rounds of the company (section 8). |
| **Contacts** | People at the company and at the other investors. |
| **Activity and events** | A quick log: date, type (meeting, call, email, board, report…), one line, **Add**. |
| **Documents** | Files linked to the deal or the company; **Upload** to add one (PDFs and images open in the browser). |
| **More details** | Source, notes, pass reason, stage history. |

When the round closes, use **Record the financing round** at the top of the page (section 7).

## 5. Web search with Claude

Claude searches the web only when you ask: never on its own, neither for a new company nor after an email. You then accept or reject each finding.

1. On a company or deal page, scroll to the **Suggestions from Claude** card at the bottom and click **Search the web** (bottom right). If the company was already searched, the button reads **Update the web search** and shows the date of the last search.
2. Claude searches for one to three minutes. You can leave the page; the card updates by itself.
3. Each finding is shown with its type, a short evidence and the link to its **source**. Findings marked *medium* or *low confidence* deserve a check.
4. Click **Accept** to save it or **Reject** to discard it. **Accept all** and **Reject all** handle the whole list.

Where accepted findings go:

| Finding | Saved as |
| --- | --- |
| Company detail (website, description, sector, city…) | The **Overview** card |
| Past financing round | A row in **Funding history**, its investors in **Investors**, and a valuation when the post-money is known |
| Person (founder, executive, board member) | A contact of the company |
| News | An entry in **Activity and events** |

The card always stays at the bottom of the page. While there is something to decide, a line at the top of the page (*N suggestions from Claude to review ↓*) takes you there. A new search does not propose what is already recorded. Each search costs a few tens of cents to a few francs; the cost of each run is in **Data › Claude jobs**.

## 6. Sending an email to the app

Forward or write an email about a company to the intake address, and the app files it for you within about two minutes.

- **Intake address:** **lakegrowthventuresmanager@gmail.com**. Only emails from authorised senders are read, and only when the mail server confirms they really come from that address; others are listed in **Inbox** as *Sender not allowed*.
- **No mailbox?** Save the email as an .eml file (Outlook: **File › Save As**; Gmail: **⋮ › Download message**) and upload it in **Inbox** with **Upload .eml**.

What happens next:

1. Claude reads the text and the PDF or image attachments (a deck, a term sheet, a report).
2. The company is matched by name or website, or created if it is new; its open deal is used, or a new one is created under *Sourced*.
3. Attachments are saved in **Documents** and the email is logged in **Activity and events**.
4. Round size, pre-money, deadline, investors, contacts and key terms appear as suggestions on the deal page, to accept or reject.
   Upcoming **general assemblies, board meetings** and other dated meetings in the email or its attachments appear as suggestions too: after **Accept** they are in **Activity and events** and in **Home** under *Upcoming assemblies and meetings*. Cash, burn, revenue and headcount stated in a report become suggested **Figures**.
   Claude puts attachments that belong together in a **folder** (for example *Series B* or *Board 2026-03*) and gives each a category.
5. No web search starts on its own: use **Search the web** on the company page if you want one (section 5).

An email from a portfolio company with a report or a convocation does not open a new deal; one about a financing round does.

Tips: one company per email; put its name in the subject; send documents as PDF (Word files are saved but not read). Emails forwarded from founders or other investors work too.

## 7. Recording a financing round (and the cap table)

One form records a whole round and builds the cap table: use it when a round closes, and once per company to load existing portfolio companies.

Open it with **Record a financing round** on the company page (top, or in the **Cap table today** card), or with **Record the financing round** on a deal at *Term sheet* or *Invested* stage, which pre-fills the round from the deal.

1. **Cap table before this round** (asked only the first time for a company): the shares outstanding per class, copied from the latest cap table or share register, e.g. *Common: 1'000'000*.
2. **The round:** name, closing date, share class (pick an existing one or type a new name, e.g. *Series A*), price per share, new shares issued to all investors. The amount raised is shown as you type.
3. **Our participation:** our shares, or the amount invested; the app computes the other from the price. Leave empty if we did not invest.
4. **After the round:** option pool (granted + ungranted), warrants, lead and other investors, notes.
5. Click **Record the round**.

Example: 1'000'000 common shares; a Series A of 250'000 shares at CHF 8; we buy 50'000 for CHF 400'000; option pool 100'000.

| Result | Value |
| --- | --- |
| Amount raised | CHF 2'000'000 |
| Post-money (8 × 1'350'000 fully diluted shares) | CHF 10'800'000 |
| Pre-money | CHF 8'800'000 |
| Our stake, basic (50'000 / 1'250'000) | 4.00% |
| Our stake, fully diluted (50'000 / 1'350'000) | 3.70% |

The app creates the share class and the new shares, our investment, the option pool, the valuation, the line in **Funding history** and its investors. If we invested, the matching deal becomes *Invested* and the company moves to **Portfolio**. If you enter a pre-money, the post-money is pre-money + amount raised instead.

For companies with several past rounds, record them in date order: the first with the shares outstanding before it, the next ones picking the class from the list. Rare cases (conversions, splits, buybacks, convertible loans) are entered in **Data**, or via **Edit classes and events** in the cap table card.

### Cap table from the documents

For a closed round you can let Claude fill the cap table from the legal documents instead of typing it.

1. Upload the documents of the closing to the company (**Documents › Upload**; put them in one folder, for example *Series B*). The most useful: the **Certificate of Incorporation**, the **Disclosure Schedule** and the **Schedule of Purchasers**.
2. In the **Cap table today** card click **Read from documents** (or **Read cap table** on the folder), tick the documents and click **Ask Claude to read them**.
3. After a few minutes a suggestion appears at the bottom of the page. Click **Show the proposed cap table** to check classes, prices, liquidation preference and seniority, shares outstanding, option pool, warrants and our holdings.
4. **Accept** records only what is missing: new classes, new shares, the option pool, the round in the funding history, the valuation and our investments. If the documents disagree with what is already recorded (fewer shares, a different fully diluted total, an investing entity that does not exist), nothing is saved and the card explains what to correct first.

For several rounds, read the documents of the oldest first.

## 8. The company page and the portfolio

The company page is the summary of everything we know about a company; its cards follow the company's phase.

- **Pipeline company:** Overview, suggestions, Funding history, Investors, Activity and events, Documents, Contacts.
- **Portfolio company:** the same, plus Our position, Company, Governance and reporting, Cap table today, Our investments, Reports and minutes.

| Card | What it shows |
| --- | --- |
| **Overview** | Description, sector, location, website, legal name, last financing round, key people, next event, number of documents. **Edit** to change. Key people and contacts are listed by role: CEO, CFO and other C-level first, then other management, the chairperson and board members, then advisors (the order comes from the *Role* text of each contact). |
| **Our position** | Stake fully diluted and basic, invested (CHF), value of our stake, multiple, IRR. |
| **Company** | Company value from the latest valuation; runway from the latest figures (cash ÷ monthly burn, less the months since); cash, burn, revenue, headcount. **+ Valuation** and **+ Figures** add new ones. |
| **Governance and reporting** | Our board role, reporting frequency, last report, last and next general assembly, data room link. |
| **Funding history** | Every known round: date, amount, pre- and post-money, investors (lead marked), source. |
| **Investors** | Every known investor, the rounds they joined, and their status in the current round. |
| **Cap table today** | Shares per class, ours, % of class; stake fully diluted and basic; checks when something does not add up. |
| **If the company is sold** | Type a sale price (for example *100M*) to see who receives what, using each class's liquidation preference, seniority and conversion, and what we receive and its multiple. Without a price it shows 0.25× to 4× the latest valuation and the sale price from which we get our money back. |

How the figures are calculated:

- **Value of our stake:** our shares × price per share of the latest valuation (or post-money × our fully diluted stake). Without a valuation it stays empty.
- **Multiple and IRR:** on the actual cash flows in CHF (investments, sales, dividends) plus today's value. Amounts in EUR or USD need an exchange rate in **Data › Exchange rates**.
- **Runway:** below 12 months it turns orange, below 6 red, and appears on **Home**.

To record a report received or a general assembly, add it in **Activity and events** with type *Report received* or *General assembly*; a future date is shown as the next assembly. The **Portfolio** page lists all portfolio companies with these figures and the totals.

## Documents

The **Documents** page finds any document of any company. Type words in the search box: Claude's PDFs and Word files are searched **inside**, the matching words are highlighted, and scanned images can only be found by title or file name. Filter by company, category (investor report, board material, charter…), folder and year.

Folders keep documents that belong together, such as all the papers of a financing round. Create one in **Documents › New folder**, and choose it in the document's **Folder** field. On the company page the folders are listed in the **Documents** card.

## 9. Users, roles and the Data section

Every user has one of three roles, set by the administrator.

| Role | Can |
| --- | --- |
| Viewer | See everything, change nothing (e.g. co-investors) |
| Editor | Add and change companies, deals, rounds and documents; accept Claude's suggestions |
| Admin | Everything an editor does, plus manage users |

The administrator adds users in **Data › Users** (or with the command in the deployment guide). There **Reset two-factor authentication** helps a colleague who lost their phone. Every change to the data is logged: who, what and when, in **Data › Log entries**.

**Data** holds every table with every field, for what the pages do not show: exchange rates, valuations, company figures, milestones, share events, convertible loans, co-investors, Claude jobs and their cost. Each form shows the main fields first; the rest is under **More details**. After saving, you return to the page you came from.

## 10. Common problems

| Problem | What to do |
| --- | --- |
| "Entered token is not valid" at login | Wait for the next code and type it quickly; check that the phone's clock is set automatically. |
| Lost or new phone | Sign in with a backup token, or ask the administrator to reset your two-factor authentication. |
| Value of our stake is empty | Add a valuation (**+ Valuation**), or record the round with its price per share. |
| Multiple or IRR missing for EUR/USD amounts | Add the exchange rate in **Data › Exchange rates**. |
| Stake looks wrong, or a warning in the cap table | Check that every class has its issued shares (section 7) and that our shares match the share register. |
| "The last search did not complete" | Read the message in the card; if it mentions the API key or credit, tell the administrator. |
| An email does not appear in **Inbox** | Check that it was sent from an authorised address; wait two minutes. |
| "Company not recognised" on an email | In **Inbox**, choose the company and click **Analyse again**. |

For the administrator: installation, updates, users from the command line and the Claude and mailbox settings are described in the deployment guide (DEPLOY.md in the code repository).
