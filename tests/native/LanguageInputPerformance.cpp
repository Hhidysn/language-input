#include <windows.h>
#include <psapi.h>

#include <algorithm>
#include <chrono>
#include <cmath>
#include <filesystem>
#include <iostream>
#include <string>
#include <vector>

#include <rime_api.h>

namespace {

constexpr int kWarmupIterations = 20;
constexpr int kMeasuredIterations = 250;
constexpr double kMaximumP95Milliseconds = 50.0;
constexpr double kMaximumColdMilliseconds = 5000.0;
constexpr size_t kMaximumWorkingSetDeltaBytes = 256ull * 1024 * 1024;

std::string Utf8Path(const std::filesystem::path& path) {
  return path.u8string();
}

size_t WorkingSetBytes() {
  PROCESS_MEMORY_COUNTERS_EX counters = {};
  counters.cb = sizeof(counters);
  if (!GetProcessMemoryInfo(
          GetCurrentProcess(),
          reinterpret_cast<PROCESS_MEMORY_COUNTERS*>(&counters),
          sizeof(counters))) {
    return 0;
  }
  return counters.WorkingSetSize;
}

bool Exercise(RimeApi* api, RimeSessionId session, const char* key_sequence) {
  api->clear_composition(session);
  if (!api->simulate_key_sequence(session, key_sequence))
    return false;

  RIME_STRUCT(RimeContext, context);
  if (!api->get_context(session, &context))
    return false;
  bool valid =
      context.menu.num_candidates > 0 && context.menu.candidates[0].comment &&
      std::string(context.menu.candidates[0].comment).find(u8"〔en·词〕") !=
          std::string::npos;
  api->free_context(&context);
  api->clear_composition(session);
  return valid;
}

double Percentile(std::vector<double> values, double percentile) {
  std::sort(values.begin(), values.end());
  size_t index = static_cast<size_t>(
      std::ceil(percentile * static_cast<double>(values.size())) - 1.0);
  return values[(std::min)(index, values.size() - 1)];
}

}  // namespace

int wmain(int argc, wchar_t** argv) {
  if (argc != 3) {
    std::cerr << "usage: LanguageInputPerformance <shared-data> <user-data>\n";
    return 2;
  }

  const std::filesystem::path shared = std::filesystem::absolute(argv[1]);
  const std::filesystem::path user = std::filesystem::absolute(argv[2]);
  std::filesystem::create_directories(user / L"logs");

  std::string shared_utf8 = Utf8Path(shared);
  std::string user_utf8 = Utf8Path(user);
  std::string log_utf8 = Utf8Path(user / L"logs");
  std::string prebuilt_utf8 = Utf8Path(shared / L"build");
  std::string staging_utf8 = Utf8Path(user / L"build");

  RimeApi* api = rime_get_api();
  if (!api)
    return 3;
  RIME_STRUCT(RimeTraits, traits);
  traits.shared_data_dir = shared_utf8.c_str();
  traits.user_data_dir = user_utf8.c_str();
  traits.prebuilt_data_dir = prebuilt_utf8.c_str();
  traits.staging_dir = staging_utf8.c_str();
  traits.log_dir = log_utf8.c_str();
  traits.distribution_name = "Language Input Performance Test";
  traits.distribution_code_name = "language-input-performance-test";
  traits.distribution_version = "1";
  traits.app_name = "rime.language-input-performance-test";
  traits.min_log_level = 2;

  api->setup(&traits);
  api->initialize(&traits);
  if (api->start_maintenance(True))
    api->join_maintenance_thread();

  const size_t baseline_working_set = WorkingSetBytes();
  RimeSessionId session = api->create_session();
  if (!session) {
    api->finalize();
    return 4;
  }

  const auto cold_start = std::chrono::steady_clock::now();
  bool valid = api->select_schema(session, "language_input_pinyin");
  api->set_option(session, "language_input_gloss", True);
  valid = valid && Exercise(api, session, "nihao");
  const auto cold_end = std::chrono::steady_clock::now();
  const double cold_ms =
      std::chrono::duration<double, std::milli>(cold_end - cold_start).count();

  for (int index = 0; valid && index < kWarmupIterations; ++index)
    valid = Exercise(api, session, "nihao");

  std::vector<double> samples;
  samples.reserve(kMeasuredIterations);
  for (int index = 0; valid && index < kMeasuredIterations; ++index) {
    const auto start = std::chrono::steady_clock::now();
    valid = Exercise(api, session, "nihao");
    const auto end = std::chrono::steady_clock::now();
    samples.push_back(
        std::chrono::duration<double, std::milli>(end - start).count());
  }

  const size_t final_working_set = WorkingSetBytes();
  const size_t working_set_delta =
      final_working_set > baseline_working_set
          ? final_working_set - baseline_working_set
          : 0;
  api->destroy_session(session);
  api->finalize();

  if (!valid || samples.size() != kMeasuredIterations) {
    std::cerr << "benchmark did not produce glossed candidates\n";
    return 5;
  }

  const double median_ms = Percentile(samples, 0.50);
  const double p95_ms = Percentile(samples, 0.95);
  const double maximum_ms = *std::max_element(samples.begin(), samples.end());
  const bool passed = cold_ms <= kMaximumColdMilliseconds &&
                      p95_ms <= kMaximumP95Milliseconds &&
                      working_set_delta <= kMaximumWorkingSetDeltaBytes;

  std::cout << "{\n"
            << "  \"iterations\": " << kMeasuredIterations << ",\n"
            << "  \"cold_ms\": " << cold_ms << ",\n"
            << "  \"median_ms\": " << median_ms << ",\n"
            << "  \"p95_ms\": " << p95_ms << ",\n"
            << "  \"max_ms\": " << maximum_ms << ",\n"
            << "  \"working_set_bytes\": " << final_working_set << ",\n"
            << "  \"working_set_delta_bytes\": " << working_set_delta << ",\n"
            << "  \"passed\": " << (passed ? "true" : "false") << "\n"
            << "}\n";
  return passed ? 0 : 1;
}
