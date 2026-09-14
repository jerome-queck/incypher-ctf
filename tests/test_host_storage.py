import check_host_storage as storage
import runtime


def test_larger_vm_capacity_keeps_historical_storage_benchmarks_advisory():
    assert runtime.PIN.disk_gib == 700
    assert storage.STORAGE_BENCHMARKS == {
        "development": {"Images": 40 * storage.GIB, "Build Cache": 20 * storage.GIB},
        "competition": {"Images": 24 * storage.GIB, "Build Cache": 0},
    }
    assert (storage.IMAGE_BENCHMARK, storage.STATE_BENCHMARK) == (
        12 * storage.GIB,
        60 * storage.GIB,
    )


def test_development_benchmark_reports_the_measured_cache_explosion():
    assert storage.advisories(
        "development",
        [
            {"Type": "Images", "Size": "39GB"},
            {"Type": "Build Cache", "Size": "64.37GB"},
        ],
        500 * storage.GIB,
    ) == ["Docker Build Cache uses 64.37GB; old development 20 GiB benchmark is advisory"]


def test_competition_reports_cache_advisory_without_refusing():
    assert storage.advisories(
        "competition",
        [
            {"Type": "Images", "Size": "20GB"},
            {"Type": "Build Cache", "Size": "1B"},
        ],
        99 * storage.GIB,
    ) == [
        "Docker Build Cache uses 1B; old competition 0 GiB benchmark is advisory",
    ]
    assert storage.advisories(
        "competition",
        [{"Type": "Build Cache", "Size": "1B"}],
        0,
    ) == ["Docker Build Cache uses 1B; old competition 0 GiB benchmark is advisory"]


def test_docker_size_parser_handles_reported_units():
    assert storage.bytes_in("10.6GB") == 10_600_000_000
    assert storage.bytes_in("2GiB") == 2 * storage.GIB
    assert storage.bytes_in("unknown") is None


def test_state_and_single_candidate_have_independent_benchmarks():
    assert storage.advisories(
        "development",
        [],
        500 * storage.GIB,
        state_size=61 * storage.GIB,
        image_sizes=(13 * storage.GIB,),
    ) == [
        "Run state uses 61 GiB; old 60 GiB benchmark is advisory",
        "one Docker image uses 13 GiB; old 12 GiB Candidate benchmark is advisory",
    ]


def test_all_host_size_measurements_are_advisory_even_for_competition():
    assert storage.advisories(
        "competition",
        [{"Type": "Images", "Size": "500GB"}, {"Type": "Build Cache", "Size": "80GB"}],
        500 * storage.GIB,
        state_size=61 * storage.GIB,
        image_sizes=(13 * storage.GIB,),
    ) == [
        "Run state uses 61 GiB; old 60 GiB benchmark is advisory",
        "one Docker image uses 13 GiB; old 12 GiB Candidate benchmark is advisory",
        "Docker Images uses 500GB; old competition 24 GiB benchmark is advisory",
        "Docker Build Cache uses 80GB; old competition 0 GiB benchmark is advisory",
    ]
    assert storage.advisories(
        "competition",
        [],
        0,
        state_size=61 * storage.GIB,
        image_sizes=(13 * storage.GIB,),
    ) == storage.advisories(
        "competition",
        [],
        500 * storage.GIB,
        state_size=61 * storage.GIB,
        image_sizes=(13 * storage.GIB,),
    )
