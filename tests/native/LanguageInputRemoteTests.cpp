#include <windows.h>

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include <boost/json.hpp>

#include "LanguageInputRemote.h"

namespace {

int failures = 0;

void Check(bool condition, const char* message) {
  if (!condition) {
    ++failures;
    std::cerr << "FAILED: " << message << '\n';
  }
}

weasel::language_input::RemoteGlossConfig TestConfig(
    const std::filesystem::path& cache) {
  weasel::language_input::RemoteGlossConfig config;
  config.enabled = true;
  config.endpoint = "https://example.invalid/v1/chat/completions";
  config.model = "test-model";
  config.api_key = "test-key";
  config.language = "en";
  config.cache_path = cache;
  return config;
}

void WriteCache(const std::filesystem::path& path,
                const std::string& language,
                const std::string& word,
                const std::string& gloss) {
  std::filesystem::create_directories(path.parent_path());
  boost::json::object entries;
  boost::json::object language_entries;
  language_entries[word] = gloss;
  entries[language] = std::move(language_entries);
  boost::json::object root;
  root["version"] = 2;
  root["model"] = "test-model";
  root["entries"] = std::move(entries);
  std::ofstream stream(path, std::ios::binary | std::ios::trunc);
  stream << boost::json::serialize(root);
}

}  // namespace

int wmain(int argc, wchar_t** argv) {
  using namespace std::chrono_literals;
  using weasel::language_input::BuildRemoteGlossRequest;
  using weasel::language_input::ParseRemoteGlossResponse;
  using weasel::language_input::RemoteGlossMap;
  using weasel::language_input::RemoteGlossService;

  if (argc != 2) {
    std::cerr << "usage: LanguageInputRemoteTests <workspace-test-cache>\n";
    return 2;
  }
  std::filesystem::path test_root =
      std::filesystem::absolute(argv[1]) /
      (L"remote-gloss-" + std::to_wstring(GetCurrentProcessId()));
  std::error_code filesystem_error;
  std::filesystem::remove_all(test_root, filesystem_error);
  std::filesystem::create_directories(test_root);

  auto config = TestConfig(test_root / L"request-cache.json");
  std::string request = BuildRemoteGlossRequest(config, {u8"缺词", u8"新词"});
  boost::system::error_code parse_error;
  auto request_json = boost::json::parse(request, parse_error);
  Check(!parse_error && request_json.is_object(),
        "the compatible chat request must be valid JSON");
  if (!parse_error && request_json.is_object()) {
    const auto& object = request_json.as_object();
    Check(object.at("model").as_string() == "test-model",
          "the selected compatible-API model must be sent");
    Check(object.at("messages").as_array().size() == 2,
          "the request must separate policy from untrusted word data");
  }

  const std::string response =
      R"({"choices":[{"message":{"content":"{\"缺词\":\"missing term\",\"extra\":\"ignore me\"}"}}]})";
  auto parsed = ParseRemoteGlossResponse(response, {u8"缺词"});
  Check(parsed && parsed->size() == 1 && parsed->at(u8"缺词") == "missing term",
        "only requested keys should survive response parsing");
  const std::string multiline =
      R"({"choices":[{"message":{"content":"{\"缺词\":\"bad\\nline\"}"}}]})";
  auto rejected = ParseRemoteGlossResponse(multiline, {u8"缺词"});
  Check(rejected && rejected->empty(),
        "multi-line model output must not enter a candidate comment");

  auto insecure = config;
  insecure.endpoint = "http://example.invalid/v1/chat/completions";
  Check(!insecure.IsUsable(), "plain HTTP must be rejected by default");
  insecure.allow_http = true;
  Check(insecure.IsUsable(), "plain HTTP requires an explicit opt-in");

  std::atomic<int> normal_calls = 0;
  std::atomic<int> completion_calls = 0;
  std::vector<std::string> received_words;
  auto normal_transport = [&](const auto&,
                              const std::vector<std::string>& words)
      -> std::optional<RemoteGlossMap> {
    ++normal_calls;
    received_words = words;
    return RemoteGlossMap{{u8"缺词", "missing term"}};
  };
  {
    RemoteGlossService service(
        TestConfig(test_root / L"normal" / L"cache.json"), normal_transport,
        [&](uintptr_t session_id) {
          if (session_id == 1)
            ++completion_calls;
        });
    service.SetSessionSensitive(1, false);
    Check(!service.Lookup(1, "en", u8"缺词"),
          "a miss should remain non-blocking before the worker finishes");
    service.QueueMissing(1, "en", {u8"缺词", u8"缺词"});
    Check(service.WaitUntilIdleForTesting(2s),
          "the fake normal request should finish");
    auto gloss = service.Lookup(1, "en", u8"缺词");
    Check(gloss && gloss->language == "en" && gloss->text == "missing term",
          "a normal async result should enter the cache");
    Check(normal_calls == 1 && received_words.size() == 1,
          "duplicate misses should be coalesced into one bounded request");
    Check(completion_calls == 1,
          "a current normal result should invoke one completion callback");
    Check(
        std::filesystem::is_regular_file(test_root / L"normal" / L"cache.json"),
        "a normal result should be persisted atomically");
  }

  std::vector<std::string> requested_languages;
  auto language_transport =
      [&](const auto& request_config,
          const std::vector<std::string>&) -> std::optional<RemoteGlossMap> {
    requested_languages.push_back(request_config.language);
    return RemoteGlossMap{{u8"文件夹",
                           request_config.language == "ja" ? u8"フォルダ"
                                                            : "folder"}};
  };
  {
    RemoteGlossService service(
        TestConfig(test_root / L"languages" / L"cache.json"),
        language_transport);
    service.SetSessionSensitive(4, false);
    service.QueueMissing(4, "en", {u8"文件夹"});
    Check(service.WaitUntilIdleForTesting(2s),
          "the English cache-isolation request should finish");
    service.QueueMissing(4, "ja", {u8"文件夹"});
    Check(service.WaitUntilIdleForTesting(2s),
          "the Japanese cache-isolation request should finish");
    auto english = service.Lookup(4, "en", u8"文件夹");
    auto japanese = service.Lookup(4, "ja", u8"文件夹");
    Check(english && english->text == "folder" && japanese &&
              japanese->text == u8"フォルダ",
          "the same candidate must keep independent language cache entries");
    Check(requested_languages == std::vector<std::string>({"en", "ja"}),
          "each queued language must reach the transport independently");
  }

  const auto lazy_cache = test_root / L"lazy" / L"cache.json";
  WriteCache(lazy_cache, "en", u8"旧词", "old value");
  {
    RemoteGlossService service(TestConfig(lazy_cache), normal_transport);
    Check(!service.Lookup(2, "en", u8"旧词"),
          "unknown sessions must be sensitive by default");
    // If the sensitive lookup had read the file, this replacement would not
    // be observed because the service loads a cache only once.
    WriteCache(lazy_cache, "en", u8"新词", "new value");
    service.SetSessionSensitive(2, false);
    auto gloss = service.Lookup(2, "en", u8"新词");
    Check(gloss && gloss->text == "new value",
          "sensitive lookups must not read the disk cache");
  }

  std::mutex gate_mutex;
  std::condition_variable gate;
  bool started = false;
  bool release = false;
  auto delayed_transport =
      [&](const auto&,
          const std::vector<std::string>&) -> std::optional<RemoteGlossMap> {
    std::unique_lock<std::mutex> lock(gate_mutex);
    started = true;
    gate.notify_all();
    gate.wait(lock, [&] { return release; });
    return RemoteGlossMap{{u8"敏感词", "sensitive term"}};
  };
  const auto dropped_cache = test_root / L"dropped" / L"cache.json";
  std::atomic<int> dropped_completions = 0;
  {
    RemoteGlossService service(TestConfig(dropped_cache), delayed_transport,
                               [&](uintptr_t) { ++dropped_completions; });
    service.SetSessionSensitive(3, false);
    const auto queue_start = std::chrono::steady_clock::now();
    service.QueueMissing(3, "en", {u8"敏感词"});
    const auto queue_elapsed = std::chrono::steady_clock::now() - queue_start;
    Check(queue_elapsed < 250ms,
          "queueing a remote miss must not block on the transport");
    {
      std::unique_lock<std::mutex> lock(gate_mutex);
      Check(gate.wait_for(lock, 2s, [&] { return started; }),
            "the delayed request should start");
    }
    service.SetSessionSensitive(3, true);
    {
      std::lock_guard<std::mutex> lock(gate_mutex);
      release = true;
    }
    gate.notify_all();
    Check(service.WaitUntilIdleForTesting(2s),
          "the invalidated request should finish and be discarded");
    service.SetSessionSensitive(3, false);
    Check(!service.Lookup(3, "en", u8"敏感词"),
          "a result completed after the sensitive transition must be dropped");
    Check(!std::filesystem::exists(dropped_cache),
          "a dropped sensitive result must not write a cache file");
    Check(dropped_completions == 0,
          "a result invalidated by sensitive mode must not refresh the UI");
  }

  std::filesystem::remove_all(test_root, filesystem_error);
  if (failures == 0)
    std::cout << "LanguageInputRemoteTests: all checks passed\n";
  return failures == 0 ? 0 : 1;
}
