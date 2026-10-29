"""Independent controls for bounded planning repair and contextual raw turns."""
import copy
from dataclasses import replace
from unittest.mock import Mock

import test_lifecycle as fixtures
from eviepoch.domain import InvalidExtraction, namespace
from eviepoch.models import AddRequest, Message, SearchRequest
from eviepoch.providers import ProviderUnavailable
from eviepoch.retrieval.disclosure_units import disclosure_units
from eviepoch.providers import Providers, StructuredOutputTruncated


class ContextRecoveryControls(fixtures.LifecycleTests):
    def test_failed_same_domain_experience_is_repaired_instead_of_silently_discarded(self):
        import json
        content = json.dumps({'dataset': 'TOY', 'task_index': 4, 'trajectory': [
            {'action': 'turn left', 'observation': 'blocked'}], 'eval_feedback': {'success': False}})
        self.add(content)
        self.provider.disclose_evidence = lambda q, p, es: {'decisions': [
            {'id': e.id, 'mode': 'exclude', 'quotes': []} for e in es]}
        self.provider.repair_disclosure = Mock(side_effect=lambda q, p, es, previous, error: {'decisions': [
            {'id': e.id, 'mode': 'retain', 'quotes': []} for e in es]})
        response = self.pipeline.search(SearchRequest('TOY task index 9', 'u', 5))
        self.assertIn('blocked', str(response))
        self.assertIn('false', str(response))
        self.provider.repair_disclosure.assert_called_once()

    def test_uncertain_answer_with_audited_partial_support_is_not_unavailability(self):
        self.add('The telescope calibration tolerance is not yet resolved.')
        self.provider.bind_obligations = lambda q, p, snap: {'obligations': [
            {'id': o.id, 'alternatives': []} for o in p.obligations]}
        self.provider.audit_evidence = lambda q, p, sources: {'status': 'uncertain', 'ids': list(sources)}
        response, trace = self.pipeline.search_with_trace(SearchRequest('telescope tolerance', 'u', 5))
        self.assertTrue(response['data'])
        self.assertEqual(trace['selection_status'], 'uncertain_fallback')
        self.provider.audit_evidence = lambda q, p, sources: {'status': 'uncertain', 'ids': []}
        with self.assertRaises(ProviderUnavailable):
            self.pipeline.search(SearchRequest('telescope tolerance', 'u', 5))

    def test_large_audit_has_room_for_all_approved_ids(self):
        import json
        from eviepoch.domain import Source, QueryPlan, stable_id
        from eviepoch.retrieval.tokens import count
        sources = {stable_id('synthetic audit source', i): Source(stable_id('synthetic audit source', i),
                   namespace('u'), 'r', 's', i, 'user', 'Supporting premise.', None, '') for i in range(120)}
        expected = {'status': 'relevant', 'ids': list(sources)}
        provider = Providers(self.config)
        def bounded_output(system, user, max_tokens=6000, stage=None):
            if count(json.dumps(expected)) > max_tokens:
                raise StructuredOutputTruncated('Synthetic output could not fit', stage=stage)
            return expected
        provider._structured = bounded_output
        self.assertEqual(provider.audit_evidence('all premises', QueryPlan(), sources), expected)
    def test_fragment_budget_keeps_multiple_sources_without_omitted_secrets(self):
        first = "Trial A measured 91 ms. Secret note is ALPHA. Trial A had zero errors."
        second = "Trial B measured 76 ms. Secret note is BETA. Trial B had zero errors."
        self.pipeline.add(AddRequest('measurements', 'u', 's', [Message('user', first), Message('user', second)]))
        self.provider.plan_value = {'intents': ['comparison'], 'obligations': [{'query': 'Trial measured errors'}]}
        self.provider.bind_obligations = lambda q, p, s: {'obligations': [
            {'id': o.id, 'alternatives': [sorted(s.sources)]} for o in p.obligations]}
        self.provider.disclose_evidence = lambda q, p, es: {'decisions': [
            {'id': e.id, 'unit_ids': [u['id'] for u in disclosure_units(e) if 'Trial' in u['text']]}
            for e in es]}
        response, trace = self.pipeline.search_with_trace(SearchRequest('Compare Trial measurements; do not disclose secret notes.', 'u', 2))
        self.assertEqual(len(response['data']), 2)
        self.assertIn('91 ms', str(response)); self.assertIn('76 ms', str(response))
        self.assertEqual(str(response).count('zero errors'), 2)
        self.assertNotIn('ALPHA', str(response)); self.assertNotIn('BETA', str(response))
        self.assertEqual(len(trace['evidence_spans']), 2)
        self.assertTrue(all(len(ids) == 2 for ids in trace['evidence_spans'].values()))
        self.assertEqual(len(trace['evidence_ranges']), 2)

    def test_structured_update_remains_one_entity_relation_value_unit(self):
        from eviepoch.retrieval.engine import Evidence
        content = 'Corpus: UPDATE: replace prior value.\n{"subject":"Delta","relation":"zone","object":"West"}'
        self.assertEqual(disclosure_units(Evidence('e', '[user] '+content, None, 1, 's')),
                         [{'id': 'u0', 'text': content}])

    def test_adjacent_units_rejoin_a_coherent_correction(self):
        content = "Correction on July 2, 2025: the earlier statement was mistaken. The value is 38 credits, not 17."
        self.add(content)
        self.provider.disclose_evidence = lambda q, p, es: {"decisions": [
            {"id": e.id, "unit_ids": [u['id'] for u in disclosure_units(e)]} for e in es]}
        response = self.pipeline.search(SearchRequest("Correction value", "u", 20))
        self.assertEqual(len(response['data']), 1)
        self.assertIn(content, response['data'][0]['content'])

    def test_coalescing_does_not_fill_an_omitted_sensitive_clause(self):
        content = "Public color is teal. Secret code is GAMMA. Public shape is round."
        self.add(content)
        self.provider.disclose_evidence = lambda q, p, es: {"decisions": [
            {"id": e.id, "unit_ids": [u['id'] for u in disclosure_units(e) if 'Public' in u['text']]}
            for e in es]}
        response = self.pipeline.search(SearchRequest("Public color and shape; do not reveal the secret code.", "u", 5))
        self.assertIn("teal", str(response)); self.assertIn("round", str(response))
        self.assertNotIn("GAMMA", str(response))

    def test_quote_refinement_does_not_union_sensitive_parent_unit(self):
        self.add("[Time: May 10, 2020, 09:15:00] Hidden Writer: Public seminar is Thursday.")
        self.provider.disclose_evidence = lambda q, p, es: {"decisions": [
            {"id": e.id, "unit_ids": [u['id'] for u in disclosure_units(e)],
             "quotes": ["Public seminar is Thursday."]} for e in es]}
        response = self.pipeline.search(SearchRequest("Seminar schedule; do not disclose author or timestamp.", "u", 4))
        self.assertTrue(response['data'])
        self.assertNotIn("Hidden Writer", str(response))
        self.assertNotIn("2020", str(response))
        self.assertIn("Thursday", str(response))

    def test_refinement_outside_parent_unit_fails_closed(self):
        self.add("Secret is GAMMA. Public seminar is Thursday.")
        self.provider.disclose_evidence = lambda q, p, es: {"decisions": [
            {"id": e.id, "unit_ids": ["u0"], "quotes": ["Public seminar is Thursday."]} for e in es]}
        with self.assertRaises(ProviderUnavailable):
            self.pipeline.search(SearchRequest("Public seminar", "u", 4))

    def test_clause_units_separate_contact_details_from_relevant_constraint(self):
        from eviepoch.retrieval.engine import Evidence
        content = "My home is West Road, my contact is Lee, and I avoid loud activities."
        units = disclosure_units(Evidence("e", "[user] " + content, None, 1, "s"))
        relevant = [u['text'] for u in units if "avoid loud" in u['text']]
        self.assertEqual(len(relevant), 1)
        self.assertNotIn("West Road", relevant[0])
        self.assertNotIn("Lee", relevant[0])

    def test_preferred_direct_premise_survives_broad_background_alternatives(self):
        self.pipeline.add(AddRequest("choices", "u", "s", [Message("user", "I enjoy reading."),
                           Message("user", "I plan to study marine biology.")]))
        def bind(q, plan, snap):
            generic = next(s.id for s in snap.sources.values() if "reading" in s.content)
            specific = next(s.id for s in snap.sources.values() if "marine biology" in s.content)
            return {"obligations": [{"id": o.id, "alternatives": [[generic]]} for o in plan.obligations],
                    "preferred_sources": [specific]}
        self.provider.bind_obligations = bind
        response = self.pipeline.search(SearchRequest("What do I plan to study?", "u", 4))
        self.assertIn("marine biology", str(response))

    def test_numbered_units_copy_exact_unicode_and_escaped_bytes(self):
        content = '数据：4.25 mg.\nNext tool argument: {"path":"x\\\\y", "message":"hello\\nworld"}.\n'
        self.add(content)
        def choose(q, p, es):
            return {"decisions": [{"id": e.id, "mode": "excerpt", "unit_ids": [u['id'] for u in disclosure_units(e)],
                                   "quotes": []} for e in es]}
        self.provider.disclose_evidence = choose
        response = self.pipeline.search(SearchRequest("数据 tool argument", "u", 20))
        self.assertTrue(response['data'])
        for item in response['data']:
            self.assertIn(item['content'].removeprefix('[user] '), content)
        self.assertTrue(any('x\\\\y' in e['content'] for e in response['data']))

    def test_numbered_units_cannot_invent_or_duplicate_a_reference(self):
        self.add("A note about the telescope.")
        for ids in (["u999"], ["u0", "u0"]):
            self.provider.disclose_evidence = lambda q, p, es: {"decisions": [
                {"id": e.id, "unit_ids": ids, "quotes": []} for e in es]}
            with self.assertRaises(ProviderUnavailable):
                self.pipeline.search(SearchRequest("telescope", "u", 4))

    def test_restricted_unit_excerpt_does_not_restore_adjacent_secret(self):
        self.add("Badge secret: GAMMA-19. The public seminar starts at 13:20.")
        def choose(q, p, es):
            return {"decisions": [{"id": e.id, "mode": "excerpt", "unit_ids": [u['id'] for u in disclosure_units(e)
                                   if 'public seminar' in u['text']], "quotes": []} for e in es]}
        self.provider.disclose_evidence = choose
        response = self.pipeline.search(SearchRequest("Public seminar time; do not disclose the badge secret.", "u", 4))
        self.assertIn("13:20", str(response))
        self.assertNotIn("GAMMA-19", str(response))

    def test_intra_message_order_uses_source_offset_not_hash_or_model_order(self):
        self.add("First topic was color. Second topic was shape. Third topic was weight.")
        self.provider.plan_value = {"intents": ["order"], "order_by": "first_mention", "obligations": []}
        def choose(q, p, es):
            return {"decisions": [{"id": e.id, "quotes": ["Third topic was weight.", "First topic was color.",
                                   "Second topic was shape."]} for e in es]}
        self.provider.disclose_evidence = choose
        response = self.pipeline.search(SearchRequest("Order of each topic", "u", 8))
        self.assertEqual([e['content'] for e in response['data']], ['[user] First topic was color.',
                         '[user] Second topic was shape.', '[user] Third topic was weight.'])

    def test_invalid_plan_receives_one_validated_repair(self):
        self.add("My report cadence is monthly.")
        self.provider.plan_value = {"intents": ["correction"], "obligations": []}
        self.provider.repair_plan = Mock(return_value={"intents": ["fact"], "obligations": [
            {"id": "o1", "query": "report cadence"}]})
        result = self.pipeline.search(SearchRequest("report cadence", "u", 4))
        self.assertTrue(result["data"])
        self.provider.repair_plan.assert_called_once()
        self.assertEqual(self.provider.repair_plan.call_args.args[2], self.provider.plan_value)

    def test_repaired_plan_is_not_allowed_to_relax_schema(self):
        self.add("The report is monthly.")
        bad = {"intents": ["invalid"], "obligations": []}
        self.provider.plan_value = copy.deepcopy(bad)
        self.provider.repair_plan = Mock(return_value=bad)
        with self.assertRaises(InvalidExtraction):
            self.pipeline.search(SearchRequest("report", "u", 4))
        self.provider.repair_plan.assert_called_once()

    def test_neighbors_are_bounded_active_same_request_and_namespace(self):
        request = AddRequest("dialogue", "u", "dialogue-session", [
            Message("user", "Let us discuss telescope calibration."),
            Message("user", "What if its temperature changes?"),
            Message("user", "Then consider refocusing."),
            Message("user", "Unrelated flight booking.")])
        self.pipeline.add(request)
        self.pipeline.add(replace(request, request_id="foreign", user_id="other"))
        self.pipeline.add(AddRequest("separate", "u", "dialogue-session", [Message("user", "Other request.")]))
        with self.pipeline.store.view(namespace("u")) as view:
            raw = view.snapshot().sources
            seed = next(s for s in raw.values() if s.request_id == "dialogue" and s.ordinal == 0)
            context = view.source_neighbors([seed], 1, 2)
            self.assertEqual({s.ordinal for s in context.sources.values()}, {0, 1})
            self.assertTrue(all(s.request_id == "dialogue" and s.namespace == namespace("u") for s in context.sources.values()))
            hidden = next(s.id for s in context.sources.values() if s.ordinal == 1)
        with self.pipeline.store.transaction(namespace("u"), True) as conn:
            conn.execute("UPDATE ee_objects SET active=0 WHERE ns=? AND id=?", (namespace("u"), hidden))
        with self.pipeline.store.view(namespace("u")) as view:
            self.assertNotIn(hidden, view.source_neighbors([seed], 1, 2).sources)
            self.assertEqual(len(view.source_neighbors([seed], 1, 0).sources), 0)

    def test_order_context_is_a_candidate_not_automatically_disclosed(self):
        self.pipeline.config = replace(self.config, window_radius=1)
        self.provider.plan_value = {"intents": ["order"], "order_by": "first_mention",
                                    "obligations": [{"id": "o", "query": "telescope calibration"}]}
        self.pipeline.add(AddRequest("turns", "u", "s", [Message("user", "Telescope calibration."),
                           Message("user", "What if its temperature changes?")]))
        self.provider.verify_candidates = lambda q, sources: {s.id for s in sources.values() if "Telescope" in s.content}
        self.provider.bind_obligations = lambda q, plan, snap: {"obligations": [
            {"id": o.id, "alternatives": [[s.id] for s in snap.sources.values() if "Telescope" in s.content]}
            for o in plan.obligations]}
        result, trace = self.pipeline.search_with_trace(SearchRequest("Order of telescope calibration discussion", "u", 4))
        self.assertEqual(trace['context_candidates'], 2)
        self.assertNotIn("temperature", str(result))


for _name in vars(fixtures.LifecycleTests):
    if _name.startswith("test_"):
        setattr(ContextRecoveryControls, _name, None)
