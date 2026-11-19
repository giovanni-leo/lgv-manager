"""Claude-assisted data gathering: API calls (faked), suggestions, email intake, pages."""

from datetime import date
from decimal import Decimal
from email.message import EmailMessage
from io import StringIO
from types import SimpleNamespace as NS
from unittest import mock

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import connection
from django.test import TestCase, override_settings

from core.ai import claude, jobs, mail
from core.ai.apply import SuggestionError, decide
from core.ai.schemas import (AttachmentInfo, CompanyFact, CompanyResearch, DealFact, EmailExtraction, InvestorRef,
                             NewsItem, PastRound, Person, RoundInvestor)
from core.models import (AiJob, CoInvestor, Company, Contact, Deal, Document, InboundEmail, Interaction,
                         RoundParticipant, Suggestion, Valuation)
from core.roles import assign_role

from .test_auth import verified_client

PDF = b"%PDF-1.4 fake deck " + b"x" * 100
AI_ON = override_settings(AI_ENABLED=True, MAIL_ALLOWED_SENDERS=["@lakegrowth.example", "partner@other.example"])


# --- Fakes ----------------------------------------------------------------------------

def usage(inp=1000, out=200, searches=0):
    return NS(input_tokens=inp, output_tokens=out, cache_read_input_tokens=0, cache_creation_input_tokens=0,
              server_tool_use=NS(web_search_requests=searches))


class FakeStream:
    def __init__(self, response):
        self.response = response

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self.response


class FakeMessages:
    def __init__(self, stream_responses=(), parse_response=None):
        self.stream_responses, self.parse_response = list(stream_responses), parse_response
        self.stream_calls, self.parse_calls = [], []

    def stream(self, **kwargs):
        self.stream_calls.append(kwargs)
        return FakeStream(self.stream_responses.pop(0))

    def parse(self, **kwargs):
        self.parse_calls.append(kwargs)
        return self.parse_response


def fake_client(**kwargs):
    messages = FakeMessages(**kwargs)
    return NS(beta=NS(messages=messages)), messages


def research_result():
    return CompanyResearch(
        company_facts=[
            CompanyFact(field="sector", value="Medical devices", source_url=None, evidence=None, confidence="high"),
            CompanyFact(field="website", value="https://cardiosense.example", source_url="https://cardiosense.example",
                        evidence="Company website", confidence="high"),
        ],
        past_rounds=[PastRound(round_name="Series B", announced_on="2025-06", amount=8_000_000, currency="CHF",
                               pre_money=None, post_money=40_000_000,
                               lead_investors=[InvestorRef(name="Alpine Health Ventures", kind="vc")],
                               other_investors=[InvestorRef(name="MedCorp AG", kind="strategic")],
                               source_url="https://news.example/series-b", confidence="high")],
        people=[
            Person(full_name="Claire Martin", role="CEO", organization="CardioSense SA", email=None, phone=None,
                   profile_url=None, source_url=None, confidence="high"),
            Person(full_name="Paul Weber", role="CFO", organization="CardioSense SA", email=None, phone=None,
                   profile_url="https://linkedin.example/paul", source_url="https://cardiosense.example/team",
                   confidence="medium"),
        ],
        news=[NewsItem(date="2026-03-12", headline="CE mark for CardioPatch", summary="CE marking obtained.",
                       category="regulatory", source_url="https://news.example/ce")],
        caveats=None,
    )


def make_eml(sender="anna@lakegrowth.example", subject="CardioSense Series C", forward=False):
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = f"Anna <{sender}>", "deals@lakegrowth.example", subject
    msg["Date"], msg["Message-ID"] = "Tue, 10 Nov 2026 09:30:00 +0100", f"<{subject.replace(' ', '')}@x>"
    msg.set_content("Hi, see the deck. Round CHF 12M at CHF 40M pre, closing 15 December.")
    if forward:
        inner = EmailMessage()
        inner["From"], inner["Subject"] = "claire@cardiosense.example", "Our Series C"
        inner.set_content("Dear investors, attached our deck.")
        inner.add_attachment(PDF, maintype="application", subtype="pdf", filename="deck.pdf")
        msg.add_attachment(inner)
    else:
        msg.add_attachment(PDF, maintype="application", subtype="pdf", filename="deck.pdf")
    msg.add_attachment(b"\x89PNG small logo", maintype="image", subtype="png", filename="logo.png")
    return msg.as_bytes()


def email_result(company_name="CardioSense"):
    return EmailExtraction(
        company_name=company_name, company_website="https://www.cardiosense.example",
        company_facts=[CompanyFact(field="city", value="Lausanne", source_url=None, evidence="Signature",
                                   confidence="high")],
        deal_facts=[DealFact(field="round_name", value="Series C", evidence=None, confidence="high"),
                    DealFact(field="round_size", value="12000000", evidence="Round CHF 12M", confidence="high"),
                    DealFact(field="pre_money", value="40000000", evidence="CHF 40M pre", confidence="high"),
                    DealFact(field="deadline", value="2026-12-15", evidence="closing 15 December",
                             confidence="medium")],
        round_investors=[RoundInvestor(name="Alpine Health Ventures", kind="vc", role="lead", status="committed",
                                       amount=5_000_000, currency="CHF", evidence=None)],
        people=[Person(full_name="Claire Martin", role="CEO", organization="CardioSense", email="claire@x.example",
                       phone=None, profile_url=None, source_url=None, confidence="high"),
                Person(full_name="Sophie Keller", role="Partner", organization="Alpine Health Ventures",
                       email=None, phone=None, profile_url=None, source_url=None, confidence="high")],
        key_terms="Series C preferred\n1x non-participating liquidation preference",
        attachments=[AttachmentInfo(filename="deck.pdf", category="pitch_deck", title="CardioSense Series C deck",
                                    document_date="2026-11-01")],
        summary="CardioSense raises a CHF 12M Series C.",
    )


# --- The API layer ------------------------------------------------------------------------

class ClaudeCallTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="CardioSense SA", city="Lausanne")

    def test_research_resumes_paused_search_and_extracts_structured_data(self):
        first = NS(stop_reason="pause_turn", usage=usage(5000, 500, 4), content=[
            NS(type="text", text="CardioSense raised a Series B. ", citations=[
                NS(url="https://news.example/series-b", title="Series B news")]),
            NS(type="web_search_tool_result", content=[NS(url="https://cardiosense.example", title="Home")]),
        ])
        second = NS(stop_reason="end_turn", usage=usage(6000, 800, 2), content=[
            NS(type="text", text="Findings: ...", citations=None)])
        parsed = NS(stop_reason="end_turn", usage=usage(3000, 600), parsed_output=research_result())
        client, messages = fake_client(stream_responses=[first, second], parse_response=parsed)
        with mock.patch.object(claude, "get_client", return_value=client):
            research, used, report = claude.research_company(self.company, ["Contact: Claire Martin"])

        self.assertEqual(len(messages.stream_calls), 2)
        call = messages.stream_calls[0]
        self.assertEqual(call["model"], "claude-opus-5-5")
        self.assertEqual({t["type"] for t in call["tools"]}, {"web_search_20260209", "web_fetch_20260209"})
        self.assertEqual(call["thinking"], {"type": "adaptive"})
        self.assertEqual((call["fallbacks"], call["betas"]), ("default", [claude.FALLBACK_BETA]))
        self.assertIn("CardioSense SA", call["messages"][0]["content"])
        self.assertIn("Claire Martin", call["messages"][0]["content"])
        resumed = messages.stream_calls[1]["messages"]
        self.assertEqual(resumed[1]["role"], "assistant")                 # paused turn sent back to resume
        self.assertEqual(len(resumed[1]["content"]), 2)
        self.assertIn("https://news.example/series-b", report)
        self.assertIn("https://cardiosense.example", report)
        self.assertIs(messages.parse_calls[0]["output_format"], CompanyResearch)
        self.assertEqual(research.past_rounds[0].round_name, "Series B")
        self.assertEqual((used.input_tokens, used.output_tokens, used.web_searches), (14000, 1900, 6))

    def test_refusal_is_reported(self):
        refused = NS(stop_reason="refusal", usage=usage(), content=[],
                     stop_details=NS(category="cyber", explanation=""))
        client, _messages = fake_client(stream_responses=[refused])
        with mock.patch.object(claude, "get_client", return_value=client), \
                self.assertRaisesMessage(claude.AiError, "declined"):
            claude.research_company(self.company)

    def test_email_attachments_sent_to_claude(self):
        parsed = NS(stop_reason="end_turn", usage=usage(), parsed_output=email_result())
        client, messages = fake_client(parse_response=parsed)
        attachments = [
            {"filename": "deck.pdf", "mime_type": "application/pdf", "data": PDF},
            {"filename": "photo.jpg", "mime_type": "image/jpeg", "data": b"jpeg"},
            {"filename": "terms.docx", "mime_type": "application/vnd.openxmlformats-officedocument."
                                                    "wordprocessingml.document", "data": b"docx"},
        ]
        with mock.patch.object(claude, "get_client", return_value=client):
            result, _used, notes = claude.extract_email("Subject", "a@b.ch", None, "Body", attachments)
        content = messages.parse_calls[0]["messages"][0]["content"]
        self.assertEqual([b["type"] for b in content], ["document", "image", "text"])
        self.assertEqual(content[0]["source"]["media_type"], "application/pdf")
        self.assertIn("terms.docx", notes[0])
        self.assertIn("send it as PDF", content[-1]["text"])
        self.assertEqual(result.company_name, "CardioSense")

    @override_settings(AI_READ_ATTACHMENTS=False)
    def test_attachments_can_be_kept_away_from_claude(self):
        blocks, notes = claude._attachment_blocks([{"filename": "deck.pdf", "mime_type": "application/pdf",
                                                    "data": PDF}])
        self.assertEqual(blocks, [])
        self.assertIn("turned off", notes[0])


# --- Web research job and applying suggestions -------------------------------------------

@AI_ON
class ResearchJobTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="CardioSense SA", sector="Medical devices", status="portfolio")
        Contact.objects.create(full_name="Claire Martin", company=self.company, role="CEO")
        self.user = User.objects.create_superuser("g", "g@example.com", "a-long-test-password")

    def run_research(self):
        job = jobs.queue_research(self.company, user=self.user)
        with mock.patch.object(claude, "research_company",
                               return_value=(research_result(), claude.Usage(10000, 2000, 5), "report")):
            return jobs.run_job(jobs.claim_next_job())

    def test_research_creates_only_new_suggestions(self):
        job = self.run_research()
        self.assertEqual(job.status, "done")
        self.assertEqual((job.input_tokens, job.web_searches), (10000, 5))
        targets = sorted(job.suggestions.values_list("target", flat=True))
        # sector unchanged and Claire already known: not proposed
        self.assertEqual(targets, ["company_field", "contact", "news", "past_round"])
        self.assertIn("4 new suggestions", job.summary)
        again = self.run_research()                                   # same findings: nothing new
        self.assertEqual(again.suggestions.count(), 0)

    def test_only_one_research_at_a_time(self):
        first = jobs.queue_research(self.company)
        self.assertEqual(jobs.queue_research(self.company), first)

    def test_accepting_each_kind(self):
        self.run_research()
        for s in Suggestion.objects.all():
            decide(s, self.user, accept=True)
        self.company.refresh_from_db()
        self.assertEqual(self.company.website, "https://cardiosense.example")
        self.assertTrue(Contact.objects.filter(full_name="Paul Weber", company=self.company, role="CFO").exists())
        valuation = Valuation.objects.get(company=self.company)
        self.assertEqual((valuation.post_money, valuation.valuation_date), (Decimal("40000000.00"), date(2025, 6, 1)))
        self.assertEqual(CoInvestor.objects.get(name="MedCorp AG").kind, "strategic")
        self.assertTrue(Interaction.objects.filter(company=self.company, summary="CE mark for CardioPatch").exists())
        self.assertFalse(Suggestion.objects.filter(status="pending").exists())
        self.assertEqual(Suggestion.objects.first().decided_by, self.user)

    def test_failure_is_recorded(self):
        jobs.queue_research(self.company)
        with mock.patch.object(claude, "research_company", side_effect=claude.AiError("No credit")):
            job = jobs.run_job(jobs.claim_next_job())
        self.assertEqual(job.status, "failed")
        self.assertIn("No credit", job.error)

    def test_invalid_values_are_not_applied(self):
        deal = Deal.objects.create(company=self.company)
        job = AiJob.objects.create(kind="email", company=self.company)
        bad = Suggestion.objects.create(job=job, company=self.company, deal=deal, target="deal_field",
                                        payload={"field": "round_size", "value": "about 10M"}, label="Round size")
        with self.assertRaises(SuggestionError):
            decide(bad, self.user, accept=True)
        bad.refresh_from_db()
        self.assertEqual(bad.status, "pending")
        country = Suggestion.objects.create(job=job, company=self.company, target="company_field",
                                            payload={"field": "country", "value": "Switzerland"}, label="Country")
        with self.assertRaisesMessage(SuggestionError, "two-letter"):
            decide(country, self.user, accept=True)


# --- Email intake -------------------------------------------------------------------------

@AI_ON
@override_settings(AI_RESEARCH_AFTER_EMAIL=False)  # covered by EmailThenResearchTests
class EmailTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="CardioSense SA", status="portfolio")
        self.user = User.objects.create_superuser("g", "g@example.com", "a-long-test-password")

    def process(self, raw, result=None, uploaded_by=None):
        inbound = mail.ingest_raw(raw, uploaded_by=uploaded_by)
        job = jobs.claim_next_job()
        if job:
            with mock.patch.object(claude, "extract_email",
                                   return_value=(result or email_result(), claude.Usage(8000, 900), [])):
                jobs.run_job(job)
        inbound.refresh_from_db()
        return inbound

    def test_parse_eml_with_forwarded_message(self):
        info = mail.parse_eml(make_eml(forward=True))
        self.assertEqual(info["sender"], "anna@lakegrowth.example")
        self.assertIn("Dear investors", info["body"])
        self.assertEqual([a["filename"] for a in info["attachments"]], ["deck.pdf"])  # small logo skipped

    def test_allowed_senders(self):
        self.assertTrue(mail.sender_allowed("ANNA@lakegrowth.example"))
        self.assertTrue(mail.sender_allowed("partner@other.example"))
        self.assertFalse(mail.sender_allowed("someone@other.example"))
        self.assertFalse(mail.sender_allowed("x@evil-lakegrowth.example"))

    def test_unknown_sender_is_not_analysed(self):
        inbound = mail.ingest_raw(make_eml(sender="stranger@spam.example"))
        self.assertEqual(inbound.status, "rejected")
        self.assertFalse(AiJob.objects.exists())

    @override_settings(MAIL_AUTH_SERVER="mx.google.com")
    def test_forged_sender_is_not_analysed(self):
        def with_auth(*headers, subject="CardioSense Series C"):
            raw = make_eml(subject=subject)
            return b"".join(f"Authentication-Results: {h}\r\n".encode() for h in headers) + raw

        genuine = ("mx.google.com; dkim=pass header.i=@lakegrowth.example header.s=s1; "
                   "spf=pass smtp.mailfrom=anna@lakegrowth.example; "
                   "dmarc=pass (p=REJECT sp=REJECT dis=NONE) header.from=lakegrowth.example")
        self.assertEqual(mail.ingest_raw(with_auth(genuine)).status, "received")
        forged = [
            (),                                                                   # no verdict at all
            ("mx.google.com; dkim=none; spf=softfail smtp.mailfrom=x@evil.example; dmarc=fail "
             "header.from=lakegrowth.example",),
            ("mx.google.com; dkim=pass header.i=@evil.example; spf=pass smtp.mailfrom=x@evil.example",),
            ("mx.google.com; spf=fail smtp.mailfrom=x@evil.example", genuine),     # fake header below Gmail's
            ("evil.example; " + genuine.split("; ", 1)[1],),                      # not our server
        ]
        for i, headers in enumerate(forged):
            inbound = mail.ingest_raw(with_auth(*headers, subject=f"Forged {i}"))
            self.assertEqual(inbound.status, "rejected", headers)
            self.assertIn("could not confirm", inbound.note)
        self.assertEqual(AiJob.objects.count(), 1)
        uploaded = mail.ingest_raw(with_auth(subject="Uploaded"), uploaded_by=self.user)  # trusted upload
        self.assertNotEqual(uploaded.status, "rejected")

    def test_email_creates_deal_documents_and_suggestions(self):
        inbound = self.process(make_eml())
        self.assertEqual(inbound.status, "done")
        self.assertEqual(inbound.company, self.company)               # "CardioSense" matched "CardioSense SA"
        deal = inbound.deal
        self.assertEqual(deal.company, self.company)
        self.assertEqual(deal.stage, "sourced")
        doc = Document.objects.get()
        self.assertEqual((doc.category, doc.title, doc.deal), ("pitch_deck", "CardioSense Series C deck", deal))
        self.assertTrue(Interaction.objects.filter(deal=deal, kind="email").exists())
        targets = sorted(Suggestion.objects.values_list("target", flat=True))
        self.assertEqual(targets, ["company_field", "contact", "contact", "deal_field", "deal_field", "deal_field",
                                   "deal_field", "deal_note", "participant"])
        for s in Suggestion.objects.all():
            decide(s, self.user, accept=True)
        deal.refresh_from_db()
        self.assertEqual((deal.round_size, deal.pre_money, deal.deadline),
                         (Decimal("12000000.00"), Decimal("40000000.00"), date(2026, 12, 15)))
        self.assertIn("1x non-participating", deal.notes)
        participant = RoundParticipant.objects.get(deal=deal)
        self.assertEqual((participant.co_investor.name, participant.role, participant.amount),
                         ("Alpine Health Ventures", "lead", Decimal("5000000.00")))
        self.assertEqual(Contact.objects.get(full_name="Sophie Keller").co_investor, participant.co_investor)

    def test_second_email_reuses_the_open_deal_and_skips_duplicate_documents(self):
        first = self.process(make_eml(subject="Series C"))
        second = self.process(make_eml(subject="Series C update"))
        self.assertEqual(second.deal, first.deal)
        self.assertEqual(Document.objects.count(), 1)
        self.assertEqual(Deal.objects.count(), 1)

    def test_new_company_is_created(self):
        inbound = self.process(make_eml(), email_result("NeuroPath SA"))
        self.assertEqual(inbound.company.name, "NeuroPath SA")
        self.assertEqual(inbound.company.status, "prospect")

    def test_unknown_company_waits_for_a_choice(self):
        inbound = self.process(make_eml(), email_result(None).model_copy(update={"company_website": None}))
        self.assertEqual(inbound.status, "needs_company")
        self.assertFalse(Deal.objects.exists())

    def test_same_email_twice_is_stored_once(self):
        raw = make_eml()
        self.assertEqual(mail.ingest_raw(raw), mail.ingest_raw(raw))
        self.assertEqual(InboundEmail.objects.count(), 1)

    @override_settings(MAIL_IMAP_HOST="imap.example", MAIL_IMAP_USER="deals@lakegrowth.example",
                       MAIL_IMAP_PASSWORD="secret")
    def test_poll_mailbox(self):
        raws = {b"1": make_eml(subject="One"), b"2": make_eml(sender="x@spam.example", subject="Two")}

        class FakeImap:
            stored = []

            def __init__(self, host, port):
                pass

            def login(self, user, password):
                pass

            def select(self, folder):
                pass

            def search(self, charset, criterion):
                return "OK", [b"1 2"]

            def fetch(self, number, parts):
                return "OK", [(b"1 (BODY[] {1}", raws[number]), b")"]

            def store(self, number, op, flag):
                FakeImap.stored.append(number)

            def logout(self):
                pass

        with mock.patch.object(mail.imaplib, "IMAP4_SSL", FakeImap):
            self.assertEqual(mail.poll_mailbox(), 2)
        self.assertEqual(FakeImap.stored, [b"1", b"2"])
        self.assertEqual(sorted(InboundEmail.objects.values_list("status", flat=True)), ["received", "rejected"])
        self.assertEqual(AiJob.objects.count(), 1)


# --- Pages and worker -----------------------------------------------------------------------

@AI_ON
class AiPageTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="NeuroPath SA")
        self.deal = Deal.objects.create(company=self.company, round_name="Series A")
        self.admin = User.objects.create_superuser("g", "g@example.com", "a-long-test-password")
        verified_client(self.client, self.admin)

    def test_search_button_queues_research_and_card_polls(self):
        self.assertContains(self.client.get(f"/deals/{self.deal.pk}/"), "Search the web")
        response = self.client.post(f"/companies/{self.company.pk}/research/?deal={self.deal.pk}")
        self.assertContains(response, "Claude is searching")
        self.assertContains(response, 'hx-trigger="every 4s"')
        job = AiJob.objects.get()
        self.assertEqual((job.company, job.deal, job.requested_by), (self.company, self.deal, self.admin))

    def test_accept_and_reject(self):
        job = AiJob.objects.create(kind="web_research", company=self.company, status="done")
        accept = Suggestion.objects.create(job=job, company=self.company, target="company_field",
                                           payload={"field": "city", "value": "Lausanne"}, label="City: Lausanne")
        reject = Suggestion.objects.create(job=job, company=self.company, target="company_field",
                                           payload={"field": "sector", "value": "Food"}, label="Sector: Food")
        page = self.client.get(f"/companies/{self.company.pk}/")
        self.assertContains(page, "City: Lausanne")
        response = self.client.post(f"/suggestions/{accept.pk}/accept/")
        self.assertEqual(response["HX-Refresh"], "true")
        self.company.refresh_from_db()
        self.assertEqual(self.company.city, "Lausanne")
        card = self.client.post(f"/suggestions/{reject.pk}/reject/")
        self.assertNotContains(card, "Sector: Food")
        self.company.refresh_from_db()
        self.assertIsNone(self.company.sector)

    def test_viewer_cannot_decide(self):
        viewer = User.objects.create_user("v", "v@example.com", "a-long-test-password")
        assign_role(viewer, "viewer")
        job = AiJob.objects.create(kind="web_research", company=self.company, status="done")
        s = Suggestion.objects.create(job=job, company=self.company, target="company_field",
                                      payload={"field": "city", "value": "Bern"}, label="City: Bern")
        verified_client(self.client, viewer)
        page = self.client.get(f"/companies/{self.company.pk}/")
        self.assertContains(page, "City: Bern")
        self.assertNotContains(page, "Search the web")
        self.assertNotContains(page, ">Accept<")
        self.assertEqual(self.client.post(f"/suggestions/{s.pk}/accept/").status_code, 403)
        self.assertEqual(self.client.post(f"/companies/{self.company.pk}/research/").status_code, 403)

    def test_new_deal_can_start_research(self):
        self.client.post("/deals/new/", {"new_company": "OrthoFlex SA", "currency": "CHF", "research": "on"})
        self.assertEqual(AiJob.objects.get().company.name, "OrthoFlex SA")

    def test_inbox_upload_and_reprocess(self):
        upload = SimpleUploadedFile("mail.eml", make_eml(sender="anyone@outside.example"), "message/rfc822")
        response = self.client.post("/inbox/", {"eml": upload})
        self.assertRedirects(response, "/inbox/", fetch_redirect_response=False)
        inbound = InboundEmail.objects.get()
        self.assertEqual(inbound.status, "received")                  # uploaded by a user: trusted
        self.assertEqual(AiJob.objects.get().inbound_email, inbound)
        self.assertContains(self.client.get("/inbox/"), "CardioSense Series C")
        InboundEmail.objects.filter(pk=inbound.pk).update(status="needs_company")
        self.client.post(f"/inbox/{inbound.pk}/reprocess/", {"company": self.company.pk})
        inbound.refresh_from_db()
        self.assertEqual((inbound.company, inbound.status), (self.company, "received"))
        self.assertEqual(AiJob.objects.count(), 2)

    @override_settings(AI_ENABLED=False)
    def test_without_api_key(self):
        page = self.client.get(f"/deals/{self.deal.pk}/")
        self.assertContains(page, "Claude is not configured")
        self.assertEqual(self.client.post(f"/companies/{self.company.pk}/research/").status_code, 400)

    def test_worker_processes_queued_jobs(self):
        jobs.queue_research(self.company)
        with mock.patch.object(claude, "research_company",
                               return_value=(research_result(), claude.Usage(), "report")):
            call_command("run_worker", "--once", stdout=StringIO())
        self.assertEqual(AiJob.objects.get().status, "done")
        self.assertTrue(Suggestion.objects.filter(company=self.company).exists())


class SchemaIntegrityTests(TestCase):
    def test_no_duplicate_foreign_keys(self):
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT conrelid::regclass, a.attname, count(*) FROM pg_constraint c
                JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
                WHERE c.contype = 'f' AND array_length(c.conkey, 1) = 1
                  AND c.connamespace = 'public'::regnamespace
                GROUP BY 1, 2 HAVING count(*) > 1""")
            self.assertEqual(cursor.fetchall(), [])


@AI_ON
class FundingHistoryTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="CardioSense SA", status="portfolio")
        self.user = User.objects.create_superuser("g", "g@example.com", "a-long-test-password")
        self.job = AiJob.objects.create(kind="web_research", company=self.company, status="done")

    def past_round(self, **extra):
        payload = {**research_result().past_rounds[0].model_dump(), **extra}
        return Suggestion.objects.create(job=self.job, company=self.company, target="past_round",
                                         payload=payload, label="Series B")

    def test_accepted_round_goes_to_funding_history(self):
        from core.models import FundingRound

        decide(self.past_round(), self.user, accept=True)
        r = FundingRound.objects.get(company=self.company)
        self.assertEqual((r.round_name, r.announced_on, r.amount, r.post_money, r.currency),
                         ("Series B", date(2025, 6, 1), Decimal("8000000.00"), Decimal("40000000.00"), "CHF"))
        self.assertEqual([i.name for i in r.leads], ["Alpine Health Ventures"])
        self.assertEqual([(i.name, i.kind) for i in r.others], [("MedCorp AG", "strategic")])
        self.assertTrue(Valuation.objects.filter(company=self.company, post_money=Decimal("40000000")).exists())
        self.assertFalse(Interaction.objects.exists())                 # no longer hidden among the news
        decide(self.past_round(other_investors=[{"name": "Venture Kick", "kind": "public"}]), self.user, True)
        self.assertEqual(FundingRound.objects.count(), 1)               # same round completed, not duplicated
        self.assertEqual(FundingRound.objects.get().investors.count(), 3)
        self.assertEqual(Valuation.objects.count(), 1)

    def test_research_skips_rounds_already_recorded(self):
        decide(self.past_round(), self.user, accept=True)
        jobs.suggestions_from_research(AiJob.objects.create(kind="web_research", company=self.company),
                                       self.company, research_result())
        self.assertFalse(Suggestion.objects.filter(status="pending", target="past_round").exists())

    def test_rounds_accepted_before_the_history_existed_are_recovered(self):
        import importlib

        from django.apps import apps

        from core.models import FundingRound

        s = self.past_round()
        Suggestion.objects.filter(pk=s.pk).update(status="accepted")
        migration = importlib.import_module("core.migrations.0007_funding_history_backfill")
        migration.backfill(apps)
        migration.backfill(apps)                                       # running twice changes nothing
        self.assertEqual(FundingRound.objects.get().round_name, "Series B")

    def test_shown_on_company_and_deal_pages(self):
        decide(self.past_round(), self.user, accept=True)
        deal = Deal.objects.create(company=self.company, round_name="Series C")
        verified_client(self.client, self.user)
        for url in (f"/companies/{self.company.pk}/", f"/deals/{deal.pk}/"):
            page = self.client.get(url)
            self.assertContains(page, "Funding history")
            self.assertContains(page, "CHF 8M")
            self.assertContains(page, "Alpine Health Ventures · lead")

    def test_known_investors_on_company_and_deal_pages(self):
        decide(self.past_round(), self.user, accept=True)
        deal = Deal.objects.create(company=self.company, round_name="Series C")
        alpine = CoInvestor.objects.get(name="Alpine Health Ventures")
        RoundParticipant.objects.create(deal=deal, co_investor=alpine, role="lead", status="committed")
        verified_client(self.client, self.user)

        company_page = self.client.get(f"/companies/{self.company.pk}/").content.decode()
        investors = company_page.split("<h2>Investors")[1].split("</section>")[0]
        self.assertIn("Alpine Health Ventures", investors)
        self.assertIn("Series B 2025 (lead)", investors)
        self.assertIn("Series C</a>: Committed", investors)
        self.assertIn("MedCorp AG", investors)
        self.assertLess(company_page.index("<h2>Overview"), company_page.index("Funding history"))
        self.assertLess(company_page.index("Funding history"), company_page.index("Cap table today"))

        deal_page = self.client.get(f"/deals/{deal.pk}/").content.decode()
        existing = deal_page.split("Existing investors in the company")[1].split("</ul>")[0]
        self.assertIn("MedCorp AG", existing)
        self.assertNotIn("Alpine Health Ventures", existing)          # already in this round
        self.client.post(f"/deals/{deal.pk}/participants/", {"investor": "MedCorp AG", "kind": "strategic",
                                                             "role": "participant", "status": "contacted"})
        added = RoundParticipant.objects.get(deal=deal, co_investor__name="MedCorp AG")
        self.assertEqual(added.status, "contacted")
        self.assertEqual(CoInvestor.objects.filter(name="MedCorp AG").count(), 1)

    def test_settled_suggestions_move_to_the_bottom(self):
        verified_client(self.client, self.user)
        url = f"/companies/{self.company.pk}/"
        s = self.past_round()
        html = self.client.get(url).content.decode()
        self.assertLess(html.index("Suggestions from Claude"), html.index("Funding history"))
        decide(s, self.user, accept=True)
        html = self.client.get(url).content.decode()
        self.assertGreater(html.index("Suggestions from Claude"), html.index("<h2>Contacts"))
        self.assertIn("Search the web", html)                         # still available, at the bottom
        deal = Deal.objects.create(company=self.company)
        html = self.client.get(f"/deals/{deal.pk}/").content.decode()
        self.assertGreater(html.index("Suggestions from Claude"), html.index("<h2>Round"))

    def test_home_lists_recent_rounds(self):
        decide(self.past_round(), self.user, accept=True)
        verified_client(self.client, self.user)
        home = self.client.get("/").content.decode()
        recent = home.split("Recent financing rounds")[1].split("</section>")[0]
        for text in ["CardioSense SA", "Series B", "CHF 8M", "Jun 2025", "Alpine Health Ventures"]:
            self.assertIn(text, recent)

    def test_old_round_news_items_are_removed(self):
        import importlib

        from django.apps import apps

        s = self.past_round()
        Suggestion.objects.filter(pk=s.pk).update(status="accepted")
        Interaction.objects.create(company=self.company, kind="news", summary=s.label, occurred_on=date(2025, 6, 1))
        keep = Interaction.objects.create(company=self.company, kind="news", summary="CE mark obtained",
                                          occurred_on=date(2026, 3, 1))
        importlib.import_module("core.migrations.0008_remove_round_news_duplicates").forwards(apps, None)
        self.assertEqual(list(Interaction.objects.all()), [keep])


@AI_ON
class EmailThenResearchTests(TestCase):
    def process(self, result):
        mail.ingest_raw(make_eml())
        with mock.patch.object(claude, "extract_email", return_value=(result, claude.Usage(), [])):
            jobs.run_job(jobs.claim_next_job())

    def test_new_company_from_email_is_researched_on_the_web(self):
        self.process(email_result("OrthoFlex SA"))
        research = AiJob.objects.get(kind="web_research")
        self.assertEqual(research.company.name, "OrthoFlex SA")
        self.assertEqual(research.status, "queued")
        self.assertIn("Web research started", AiJob.objects.get(kind="email").summary)

    def test_well_known_company_is_not_researched_again(self):
        Company.objects.create(name="CardioSense SA", description="Known already.")
        self.process(email_result("CardioSense"))
        self.assertFalse(AiJob.objects.filter(kind="web_research").exists())

    @override_settings(AI_RESEARCH_AFTER_EMAIL=False)
    def test_can_be_turned_off(self):
        self.process(email_result("OrthoFlex SA"))
        self.assertFalse(AiJob.objects.filter(kind="web_research").exists())
