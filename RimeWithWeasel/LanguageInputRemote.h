#pragma once

#include <chrono>
#include <cstdint>
#include <filesystem>
#include <functional>
#include <map>
#include <memory>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

namespace weasel::language_input {

struct RemoteGloss {
  std::string language;
  std::string text;

  std::string MarkedComment() const;
};

struct RemoteGlossConfig {
  bool enabled = false;
  bool allow_http = false;
  std::string endpoint = "https://api.openai.com/v1/chat/completions";
  std::string model = "gpt-4.1-mini";
  std::string api_key;
  std::string language = "en";
  std::filesystem::path cache_path;
  bool use_local_host = false;
  std::filesystem::path local_host_executable;
  std::filesystem::path local_host_catalog;
  std::filesystem::path local_host_m2m100_catalog;
  std::filesystem::path local_host_models;
  int connect_timeout_ms = 5000;
  int request_timeout_ms = 15000;

  bool IsUsable() const;
};

using RemoteGlossMap = std::map<std::string, std::string>;
enum class RemoteGlossError {
  kNone,
  kTransport,
  kMissingModel,
  kMissingRuntime,
  kInvalidResponse,
};

struct RemoteGlossTransportResult {
  std::optional<RemoteGlossMap> glosses;
  RemoteGlossError error = RemoteGlossError::kTransport;
};

using RemoteGlossTransport = std::function<RemoteGlossTransportResult(
    const RemoteGlossConfig&,
    const std::vector<std::string>&)>;
using RemoteGlossCompletion = std::function<void(uintptr_t)>;

// Remote access is disabled unless LANGUAGE_INPUT_REMOTE_ENABLED is true.
// The API key is read only in that case and is never written to configuration.
RemoteGlossConfig LoadRemoteGlossConfig(
    const std::filesystem::path& cache_path);

// Exposed for deterministic parser tests and compatible-API diagnostics.
std::string BuildRemoteGlossRequest(const RemoteGlossConfig& config,
                                    const std::vector<std::string>& words);
std::optional<RemoteGlossMap> ParseRemoteGlossResponse(
    std::string_view response,
    const std::vector<std::string>& requested_words);

class RemoteGlossService {
 public:
  explicit RemoteGlossService(RemoteGlossConfig config,
                              RemoteGlossTransport transport = {},
                              RemoteGlossCompletion completion = {});
  ~RemoteGlossService();

  RemoteGlossService(const RemoteGlossService&) = delete;
  RemoteGlossService& operator=(const RemoteGlossService&) = delete;

  bool available() const;

  // Unknown sessions are sensitive by default. Call this with false only
  // after the platform input-scope check has completed.
  void SetSessionSensitive(uintptr_t session_id, bool sensitive);
  void RemoveSession(uintptr_t session_id);
  void SuspendAllSessions();

  std::optional<RemoteGloss> Lookup(uintptr_t session_id,
                                    std::string_view language,
                                    std::string_view word);
  std::optional<RemoteGloss> Lookup(uintptr_t session_id,
                                    std::string_view language,
                                    std::string_view word,
                                    std::string_view model);
  void QueueMissing(uintptr_t session_id,
                    std::string_view language,
                    const std::vector<std::string>& words);
  void QueueMissing(uintptr_t session_id,
                    std::string_view language,
                    const std::vector<std::string>& words,
                    std::string_view model);

  // Preload a local QuickMT route after a normal input field gains focus.
  // This sends only the language and model, never candidate text.
  void PrepareLocalModel(uintptr_t session_id,
                         std::string_view language,
                         std::string_view model);

  // Errors are consumed by the UI refresh path. Sensitive sessions never
  // expose an error and never retain one for later display.
  RemoteGlossError TakeLastError(uintptr_t session_id);
  void InvalidateSession(uintptr_t session_id);

  // Used by the native test executable; production never waits for requests.
  bool WaitUntilIdleForTesting(std::chrono::milliseconds timeout);

 private:
  class Impl;
  std::unique_ptr<Impl> impl_;
};

}  // namespace weasel::language_input
