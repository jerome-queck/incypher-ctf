import check_host_storage as storage


def test_development_budget_rejects_the_measured_cache_explosion():
    assert storage.violations(
        "development",
        [
            {"Type": "Images", "Size": "39GB"},
            {"Type": "Build Cache", "Size": "64.37GB"},
        ],
        500 * storage.GIB,
    ) == ["Docker Build Cache uses 64.37GB; development limit is 20 GiB"]


def test_competition_requires_zero_cache_and_protected_host_space():
    assert storage.violations(
        "competition",
        [
            {"Type": "Images", "Size": "20GB"},
            {"Type": "Build Cache", "Size": "1B"},
        ],
        99 * storage.GIB,
    ) == [
        "Working has 99 GiB free; 100 GiB is protected",
        "Docker Build Cache uses 1B; competition limit is 0 GiB",
    ]


def test_docker_size_parser_handles_reported_units():
    assert storage.bytes_in("10.6GB") == 10_600_000_000
    assert storage.bytes_in("2GiB") == 2 * storage.GIB
    assert storage.bytes_in("unknown") is None


def test_state_and_single_candidate_have_independent_limits():
    assert storage.violations(
        "development",
        [],
        500 * storage.GIB,
        state_size=61 * storage.GIB,
        image_sizes=(13 * storage.GIB,),
    ) == [
        "Run state uses 61 GiB; limit is 60 GiB",
        "one Docker image uses 13 GiB; Candidate limit is 12 GiB",
    ]
