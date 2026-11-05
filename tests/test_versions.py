"""版本与版本区间：SemVer 的排序规则，以及区间代数。"""

import unittest

from ravel.errors import VersionError
from ravel.versions import (
    ANY, NOTHING, Interval, Version, VersionRange, caret_of, format_constraint,
    parse_constraint, sort_versions,
)


class ParseTests(unittest.TestCase):
    def test_three_segments(self):
        version = Version.parse("1.2.3")
        self.assertEqual((version.major, version.minor, version.patch), (1, 2, 3))
        self.assertFalse(version.is_prerelease)

    def test_missing_segments_default_to_zero(self):
        self.assertEqual(Version.parse("1"), Version(1, 0, 0))
        self.assertEqual(Version.parse("1.2"), Version(1, 2, 0))

    def test_leading_v_is_allowed(self):
        self.assertEqual(Version.parse("v1.2.3"), Version(1, 2, 3))

    def test_whitespace_is_stripped(self):
        self.assertEqual(Version.parse("  1.2.3  "), Version(1, 2, 3))

    def test_prerelease_identifiers(self):
        version = Version.parse("1.0.0-alpha.1")
        self.assertTrue(version.is_prerelease)
        self.assertEqual(version.prerelease, ("alpha", 1))

    def test_numeric_prerelease_identifiers_become_ints(self):
        self.assertEqual(Version.parse("1.0.0-0.3.7").prerelease, (0, 3, 7))

    def test_build_metadata(self):
        version = Version.parse("1.0.0+build.5")
        self.assertEqual(version.build, "build.5")

    def test_build_metadata_does_not_affect_equality(self):
        self.assertEqual(Version.parse("1.0.0+a"), Version.parse("1.0.0+b"))

    def test_build_metadata_does_not_affect_order(self):
        self.assertFalse(Version.parse("1.0.0+a") < Version.parse("1.0.0+b"))
        self.assertFalse(Version.parse("1.0.0+b") < Version.parse("1.0.0+a"))

    def test_invalid_versions(self):
        for text in ("", "abc", "1.2.3.4", "1..2", "1.2.3-", "-1.0.0", "1.0.0-"):
            with self.subTest(text=text):
                with self.assertRaises(VersionError):
                    Version.parse(text)

    def test_error_message_quotes_the_input(self):
        with self.assertRaises(VersionError) as caught:
            Version.parse("nope")
        self.assertIn("nope", caught.exception.message)

    def test_coerce(self):
        self.assertEqual(Version.coerce("1.2.3"), Version(1, 2, 3))
        self.assertEqual(Version.coerce(2), Version(2, 0, 0))
        version = Version(3, 0, 0)
        self.assertIs(Version.coerce(version), version)

    def test_round_trip(self):
        for text in ("1.2.3", "1.0.0-rc.1", "2.0.0+build", "1.2.3-beta+exp.sha.5114f85"):
            with self.subTest(text=text):
                self.assertEqual(str(Version.parse(text)), text)


class OrderingTests(unittest.TestCase):
    def test_patch_minor_major(self):
        self.assertLess(Version.parse("1.0.0"), Version.parse("1.0.1"))
        self.assertLess(Version.parse("1.0.1"), Version.parse("1.1.0"))
        self.assertLess(Version.parse("1.1.0"), Version.parse("2.0.0"))

    def test_prerelease_is_lower_than_release(self):
        self.assertLess(Version.parse("1.0.0-alpha"), Version.parse("1.0.0"))

    def test_prerelease_ordering_of_identifiers(self):
        chain = ["1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-alpha.beta",
                 "1.0.0-beta", "1.0.0-beta.2", "1.0.0-beta.11", "1.0.0-rc.1",
                 "1.0.0"]
        for lower, higher in zip(chain, chain[1:]):
            with self.subTest(lower=lower, higher=higher):
                self.assertLess(Version.parse(lower), Version.parse(higher))

    def test_numeric_identifier_is_lower_than_alphanumeric(self):
        self.assertLess(Version.parse("1.0.0-1"), Version.parse("1.0.0-alpha"))

    def test_fewer_identifiers_is_lower(self):
        self.assertLess(Version.parse("1.0.0-alpha"), Version.parse("1.0.0-alpha.1"))

    def test_sorting(self):
        versions = [Version.parse(t) for t in ("2.0.0", "1.0.0", "1.0.0-rc.1", "1.2.0")]
        self.assertEqual([str(v) for v in sort_versions(versions)],
                         ["2.0.0", "1.2.0", "1.0.0", "1.0.0-rc.1"])
        self.assertEqual([str(v) for v in sort_versions(versions, reverse=False)],
                         ["1.0.0-rc.1", "1.0.0", "1.2.0", "2.0.0"])

    def test_comparison_with_other_types(self):
        with self.assertRaises(TypeError):
            Version.parse("1.0.0") < "2.0.0"  # noqa: B015 - 就是要它报错


class BumpTests(unittest.TestCase):
    def test_bumps(self):
        version = Version.parse("1.2.3-alpha")
        self.assertEqual(str(version.bump_major()), "2.0.0")
        self.assertEqual(str(version.bump_minor()), "1.3.0")
        self.assertEqual(str(version.bump_patch()), "1.2.4")

    def test_release_tuple(self):
        self.assertEqual(Version.parse("1.2.3-rc.1").release, (1, 2, 3))


class IntervalTests(unittest.TestCase):
    def test_contains_with_bounds(self):
        interval = Interval(Version(1, 0, 0), True, Version(2, 0, 0), False)
        self.assertTrue(interval.contains(Version(1, 0, 0)))
        self.assertTrue(interval.contains(Version(1, 5, 0)))
        self.assertFalse(interval.contains(Version(2, 0, 0)))
        self.assertFalse(interval.contains(Version(0, 9, 0)))

    def test_exclusive_lower_bound(self):
        interval = Interval(Version(1, 0, 0), False, None, False)
        self.assertFalse(interval.contains(Version(1, 0, 0)))
        self.assertTrue(interval.contains(Version(1, 0, 1)))

    def test_empty_when_bounds_cross(self):
        self.assertTrue(Interval(Version(2, 0, 0), True, Version(1, 0, 0), True).is_empty)

    def test_empty_when_bounds_touch_exclusively(self):
        self.assertTrue(Interval(Version(1, 0, 0), False, Version(1, 0, 0), True).is_empty)
        self.assertFalse(Interval(Version(1, 0, 0), True, Version(1, 0, 0), True).is_empty)

    def test_is_exact(self):
        self.assertTrue(Interval(Version(1, 0, 0), True, Version(1, 0, 0), True).is_exact)
        self.assertFalse(Interval(Version(1, 0, 0), True, Version(1, 0, 0), False).is_exact)

    def test_render(self):
        self.assertEqual(Interval().render(), "*")
        self.assertEqual(Interval(upper=Version(2, 0, 0)).render(), "<2.0.0")
        self.assertEqual(Interval(upper=Version(2, 0, 0), upper_inclusive=True).render(), "<=2.0.0")
        self.assertEqual(Interval(lower=Version(1, 0, 0)).render(), ">=1.0.0")
        self.assertEqual(Interval(lower=Version(1, 0, 0), lower_inclusive=False).render(), ">1.0.0")
        self.assertEqual(
            Interval(Version(1, 0, 0), True, Version(2, 0, 0), False).render(),
            ">=1.0.0, <2.0.0")
        self.assertEqual(Interval(Version(1, 0, 0), True, Version(1, 0, 0), True).render(), "1.0.0")


class RangeConstructionTests(unittest.TestCase):
    def test_exact(self):
        ranges = VersionRange.exact(Version(1, 2, 3))
        self.assertEqual(ranges.exact_version, Version(1, 2, 3))
        self.assertTrue(ranges.contains(Version(1, 2, 3)))
        self.assertFalse(ranges.contains(Version(1, 2, 4)))

    def test_at_least_and_at_most(self):
        self.assertTrue(VersionRange.at_least(Version(1, 0, 0)).contains(Version(9, 0, 0)))
        self.assertFalse(VersionRange.at_least(Version(1, 0, 0), False).contains(Version(1, 0, 0)))
        self.assertTrue(VersionRange.at_most(Version(2, 0, 0)).contains(Version(2, 0, 0)))
        self.assertFalse(VersionRange.at_most(Version(2, 0, 0), False).contains(Version(2, 0, 0)))

    def test_any_and_nothing(self):
        self.assertTrue(ANY.is_any)
        self.assertFalse(ANY.is_empty)
        self.assertTrue(NOTHING.is_empty)
        self.assertTrue(ANY.contains(Version(0, 0, 1)))
        self.assertFalse(NOTHING.contains(Version(1, 0, 0)))

    def test_adjacent_intervals_are_merged(self):
        ranges = VersionRange.build([Interval(upper=Version(2, 0, 0)),
                                     Interval(Version(2, 0, 0), True, None, False)])
        self.assertEqual(len(ranges.intervals), 1)
        self.assertTrue(ranges.is_any)

    def test_overlapping_intervals_are_merged(self):
        ranges = VersionRange.build([
            Interval(Version(1, 0, 0), True, Version(3, 0, 0), False),
            Interval(Version(2, 0, 0), True, Version(4, 0, 0), False),
        ])
        self.assertEqual(len(ranges.intervals), 1)
        self.assertTrue(ranges.contains(Version(3, 5, 0)))

    def test_gap_is_preserved(self):
        ranges = VersionRange.build([
            Interval(upper=Version(2, 0, 0)),
            Interval(Version(3, 0, 0), True, None, False),
        ])
        self.assertEqual(len(ranges.intervals), 2)
        self.assertFalse(ranges.contains(Version(2, 5, 0)))

    def test_empty_intervals_are_dropped(self):
        ranges = VersionRange.build([Interval(Version(2, 0, 0), True, Version(1, 0, 0), True)])
        self.assertTrue(ranges.is_empty)

    def test_render(self):
        self.assertEqual(ANY.render(), "*")
        self.assertEqual(NOTHING.render(), "<没有版本>")
        self.assertEqual(VersionRange.exact(Version(1, 0, 0)).render(), "1.0.0")
        self.assertEqual(
            VersionRange.build([Interval(upper=Version(2, 0, 0)),
                                Interval(Version(3, 0, 0), True, None, False)]).render(),
            "<2.0.0 或 >=3.0.0")


class RangeAlgebraTests(unittest.TestCase):
    def setUp(self):
        self.one = parse_constraint(">=1.0.0, <3.0.0")
        self.two = parse_constraint(">=2.0.0, <4.0.0")

    def test_intersect(self):
        both = self.one.intersect(self.two)
        self.assertTrue(both.contains(Version(2, 5, 0)))
        self.assertFalse(both.contains(Version(1, 5, 0)))
        self.assertFalse(both.contains(Version(3, 5, 0)))

    def test_intersect_is_commutative(self):
        self.assertEqual(self.one.intersect(self.two), self.two.intersect(self.one))

    def test_intersect_with_self_returns_self(self):
        self.assertIs(self.one.intersect(self.one), self.one)

    def test_union(self):
        both = self.one.union(self.two)
        self.assertEqual(both.render(), ">=1.0.0, <4.0.0")

    def test_union_with_self(self):
        self.assertIs(self.one.union(self.one), self.one)

    def test_union_is_idempotent(self):
        self.assertEqual(self.one.union(self.one), self.one)

    def test_complement_of_any_is_nothing(self):
        self.assertTrue(ANY.complement().is_empty)

    def test_complement_of_nothing_is_any(self):
        self.assertTrue(NOTHING.complement().is_any)

    def test_complement_is_an_involution(self):
        for text in ("^1.2", ">=1.0 <2.0 || >=3.0", "!=1.5.0", "1.2.3"):
            with self.subTest(text=text):
                ranges = parse_constraint(text)
                self.assertEqual(ranges.complement().complement(), ranges)

    def test_complement_leaves_a_hole(self):
        complement = VersionRange.exact(Version(2, 0, 0)).complement()
        self.assertEqual(len(complement.intervals), 2)
        self.assertFalse(complement.contains(Version(2, 0, 0)))
        self.assertTrue(complement.contains(Version(1, 9, 9)))

    def test_difference(self):
        rest = self.one.difference(self.two)
        self.assertTrue(rest.contains(Version(1, 5, 0)))
        self.assertFalse(rest.contains(Version(2, 5, 0)))

    def test_difference_with_self_is_empty(self):
        self.assertTrue(self.one.difference(self.one).is_empty)

    def test_subset(self):
        inner = parse_constraint(">=1.5.0, <2.0.0")
        self.assertTrue(inner.is_subset_of(self.one))
        self.assertFalse(self.one.is_subset_of(inner))

    def test_everything_is_a_subset_of_any(self):
        self.assertTrue(self.one.is_subset_of(ANY))
        self.assertTrue(NOTHING.is_subset_of(self.one))

    def test_subset_of_self(self):
        self.assertTrue(self.one.is_subset_of(self.one))

    def test_nothing_is_not_a_superset(self):
        self.assertFalse(ANY.is_subset_of(NOTHING))

    def test_disjoint(self):
        other = parse_constraint(">=4.0.0")
        self.assertTrue(self.one.is_disjoint(other))
        self.assertFalse(self.one.is_disjoint(self.two))

    def test_operators(self):
        self.assertEqual(self.one & self.two, self.one.intersect(self.two))
        self.assertEqual(self.one | self.two, self.one.union(self.two))
        self.assertEqual(~self.one, self.one.complement())

    def test_equality_means_same_version_set(self):
        left = VersionRange.build([Interval(Version(1, 0, 0), True, Version(2, 0, 0), False)])
        right = parse_constraint(">=1.0.0, <2.0.0")
        self.assertEqual(left, right)
        self.assertEqual(hash(left), hash(right))

    def test_ranges_are_hashable(self):
        self.assertEqual(len({ANY, NOTHING, parse_constraint("^1.0")}), 3)


class ConstraintSyntaxTests(unittest.TestCase):
    def test_star(self):
        for text in ("*", "x", "X", "any", ""):
            with self.subTest(text=text):
                self.assertTrue(parse_constraint(text).is_any)

    def test_caret_on_major(self):
        self.assertEqual(parse_constraint("^1.2.3").render(), ">=1.2.3, <2.0.0")

    def test_caret_on_zero_minor(self):
        self.assertEqual(parse_constraint("^0.2.3").render(), ">=0.2.3, <0.3.0")

    def test_caret_on_zero_zero(self):
        self.assertEqual(parse_constraint("^0.0.3").render(), ">=0.0.3, <0.0.4")

    def test_caret_with_one_segment(self):
        self.assertEqual(parse_constraint("^1").render(), ">=1.0.0, <2.0.0")
        self.assertEqual(parse_constraint("^0").render(), ">=0.0.0, <1.0.0")

    def test_caret_with_two_segments(self):
        self.assertEqual(parse_constraint("^0.2").render(), ">=0.2.0, <0.3.0")

    def test_tilde(self):
        self.assertEqual(parse_constraint("~1.2.3").render(), ">=1.2.3, <1.3.0")
        self.assertEqual(parse_constraint("~1.2").render(), ">=1.2.0, <1.3.0")
        self.assertEqual(parse_constraint("~1").render(), ">=1.0.0, <2.0.0")

    def test_wildcard(self):
        self.assertEqual(parse_constraint("1.2.*").render(), ">=1.2.0, <1.3.0")
        self.assertEqual(parse_constraint("1.*").render(), ">=1.0.0, <2.0.0")
        self.assertEqual(parse_constraint("1.2.x").render(), ">=1.2.0, <1.3.0")

    def test_comparators(self):
        self.assertEqual(parse_constraint(">=1.0").render(), ">=1.0.0")
        self.assertEqual(parse_constraint(">1.0").render(), ">1.0.0")
        self.assertEqual(parse_constraint("<=1.0").render(), "<=1.0.0")
        self.assertEqual(parse_constraint("<1.0").render(), "<1.0.0")
        self.assertEqual(parse_constraint("=1.0").render(), "1.0.0")
        self.assertEqual(parse_constraint("==1.0").render(), "1.0.0")
        self.assertEqual(parse_constraint("1.0").render(), "1.0.0")

    def test_not_equal(self):
        ranges = parse_constraint("!=1.5.0")
        self.assertFalse(ranges.contains(Version(1, 5, 0)))
        self.assertTrue(ranges.contains(Version(1, 5, 1)))

    def test_conjunction_with_separators(self):
        self.assertEqual(parse_constraint(">=1.0, <2.0").render(), ">=1.0.0, <2.0.0")
        self.assertEqual(parse_constraint(">=1.0 <2.0").render(), ">=1.0.0, <2.0.0")

    def test_disjunction(self):
        self.assertEqual(parse_constraint(">=1.0 <1.2 || >=2.0").render(),
                         ">=1.0.0, <1.2.0 或 >=2.0.0")

    def test_disjunction_with_three_parts(self):
        ranges = parse_constraint("1.0.0 || 2.0.0 || 3.0.0")
        self.assertEqual(len(ranges.intervals), 3)

    def test_empty_conjunction_yields_nothing(self):
        self.assertTrue(parse_constraint(">=2.0 <1.0").is_empty)

    def test_prerelease_constraints(self):
        self.assertEqual(parse_constraint("^2.0.0-rc.1").render(), ">=2.0.0-rc.1, <3.0.0")
        self.assertTrue(parse_constraint(">=2.0.0-0").contains(Version.parse("2.0.0-rc.1")))

    def test_invalid_constraints(self):
        for text in ("^", "~", "^^1.0", ">=", "1.2.3.4", "1.2.3.*"):
            with self.subTest(text=text):
                with self.assertRaises(VersionError):
                    parse_constraint(text)

    def test_caret_of(self):
        self.assertEqual(caret_of(Version(1, 2, 3)).render(), ">=1.2.3, <2.0.0")


class FormatTests(unittest.TestCase):
    def test_round_trips_through_caret_and_tilde(self):
        cases = {"^2": ">=2.0.0, <3.0.0", "~1.2": ">=1.2.0, <1.3.0",
                 "~1.2.3": ">=1.2.3, <1.3.0", "1.2.3": "1.2.3", "*": "*"}
        for text, expected in cases.items():
            with self.subTest(text=text):
                ranges = parse_constraint(text)
                self.assertEqual(ranges.render(), expected)
                self.assertEqual(format_constraint(ranges), text)

    def test_trailing_zero_segments_are_trimmed(self):
        self.assertEqual(format_constraint(parse_constraint("^2.0")), "^2")
        self.assertEqual(format_constraint(parse_constraint("^1.2.0")), "^1.2")

    def test_falls_back_to_interval_rendering(self):
        self.assertEqual(format_constraint(parse_constraint(">=1.0 <1.2")), ">=1.0.0, <1.2.0")

    def test_empty_and_any(self):
        self.assertEqual(format_constraint(NOTHING), "<没有版本>")
        self.assertEqual(format_constraint(ANY), "*")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
