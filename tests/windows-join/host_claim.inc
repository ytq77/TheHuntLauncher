// SPDX-License-Identifier: GPL-2.0-or-later
// Include in the existing native HTTP fixture, after its emulator functions.
int test_bound_http() {
    prepare_slots();
    using namespace TheHunt::Summon;
    auto& native_claim_call = NativeClaimCall();
    const std::string get = test_origin + "/summon_messenger/get?user_id=71";
    const std::string claim = test_origin + "/summon_messenger/request?user_id=71";
    const std::string body = R"({"SessionId":"host","TargetUserId":72,"TargetCharaId":12})";
    const std::string registration(48, 'b');
    const auto origin35 = EncodeClaimOrigin({registration, 35, 16, 10000, 1, HostRunId()}).value();
    const auto origin36 = EncodeClaimOrigin({registration, 36, 16, 11000, 2, HostRunId()}).value();
    const std::string oldPose = "1,18010000,3f800000,40000000,40400000,00000000,241010";
    const std::string newPose = "1,18010000,40000000,40400000,40800000,3f800000,241010";
    auto open = [&](const std::string& url) { return CreateRequestWithURL(1, 1, url.c_str(), 0); };
    auto send = [&](int request) { return SendRequest(request, body.data(), body.size()); };

    const int discovery = open(get);
    assert(send(discovery) == 0 && headers_added[discovery].at(discovery_header) == "1");
    assert(!headers_added[discovery].contains(claim_origin_header));
    assert(!headers_added[discovery].contains("X-ShadPS4-Bloodborne-Host-Placement"));

    native_claim_call.begin(401, 88, ClaimHeaders{origin35, oldPose});
    const int old = open(claim);
    assert(send(old) < 0); // Return not observed; must not send the bare legacy request.
    native_claim_call.returned(401, 88);
    native_claim_call.begin(401, 88, ClaimHeaders{origin36, newPose});
    const int current = open(claim);
    native_claim_call.returned(401, 88);
    assert(send(current) == 0 && send(old) == 0 && send(current) == 0 && sent_body == body);
    assert(headers_added[old].at(claim_origin_header) == origin35);
    assert(headers_added[old].at("X-ShadPS4-Bloodborne-Host-Placement") == oldPose);
    assert(headers_added[current].at(claim_origin_header) == origin36);
    assert(headers_added[current].at("X-ShadPS4-Bloodborne-Host-Placement") == newPose);

    native_claim_call.begin(500, 88, ClaimHeaders{origin35, oldPose});
    const int unclosed = open(claim);
    assert(DeleteRequest(unclosed) == 0);
    next_request = unclosed; // Native HLE may later recycle a numeric request handle.
    native_claim_call.begin(501, 88, ClaimHeaders{origin36, newPose});
    const int reused = open(claim);
    native_claim_call.returned(500, 88);
    native_claim_call.returned(501, 88);
    assert(reused == unclosed && send(reused) < 0);

    native_claim_call.begin(510, 88, ClaimHeaders{origin35, oldPose});
    const int first = open(claim), second = open(claim);
    native_claim_call.returned(510, 88);
    assert(send(first) < 0 && send(second) < 0 && send(open(claim)) < 0);
    const int elsewhere = open("http://127.0.0.1:31416/summon_messenger/request?user_id=71");
    assert(send(elsewhere) < 0 && !headers_added[elsewhere].contains(claim_origin_header));
    test_private = false;
    assert(send(current) < 0);
    assert(sent == 4);
    std::cout << "PASS actual HTTP wrappers: reverse sends, pose, retry, reuse, and missing handoff\n";
    return 0;
}
