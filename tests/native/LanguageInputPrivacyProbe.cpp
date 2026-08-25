#include <filesystem>
#include <iostream>
#include <string>

#include <rime_api.h>

namespace {

std::string Utf8Path(const std::filesystem::path& path) {
  return path.u8string();
}

}  // namespace

int wmain(int argc, wchar_t** argv) {
  if (argc != 4) {
    std::cerr << "usage: LanguageInputPrivacyProbe <shared-data> <user-data> "
                 "<normal|sensitive>\n";
    return 2;
  }
  const std::filesystem::path shared = std::filesystem::absolute(argv[1]);
  const std::filesystem::path user = std::filesystem::absolute(argv[2]);
  const bool sensitive = std::wstring(argv[3]) == L"sensitive";
  if (!sensitive && std::wstring(argv[3]) != L"normal") {
    std::cerr << "invalid mode\n";
    return 2;
  }
  std::filesystem::create_directories(user);
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
  traits.distribution_name = "Language Input Privacy Test";
  traits.distribution_code_name = "language-input-privacy-test";
  traits.distribution_version = "1";
  traits.app_name = "rime.language-input-privacy-test";
  traits.min_log_level = 2;

  api->setup(&traits);
  api->initialize(&traits);
  char actual_user_data[4096] = {};
  api->get_user_data_dir_s(actual_user_data, sizeof(actual_user_data));
  if (std::filesystem::path(actual_user_data) != user) {
    std::cerr << "user-data path mismatch: " << actual_user_data << '\n';
    api->finalize();
    return 6;
  }
  if (api->start_maintenance(True))
    api->join_maintenance_thread();
  RimeSessionId session = api->create_session();
  if (!session) {
    api->finalize();
    return 4;
  }
  bool ok = api->select_schema(session, "language_input_pinyin");
  api->set_option(session, "language_input_sensitive",
                  sensitive ? True : False);
  api->set_option(session, "language_input_gloss", True);

  // Repeating a system-dictionary selection reliably creates or increments a
  // normal user-dictionary entry. The privacy patch must suppress every one of
  // these commits while the sensitive option is active.
  for (int attempt = 0; ok && attempt < 3; ++attempt) {
    ok = api->simulate_key_sequence(session, "nihao");
    ok = ok && api->select_candidate_on_current_page(session, 0);
    RIME_STRUCT(RimeCommit, commit);
    if (!api->get_commit(session, &commit) || !commit.text ||
        std::string(commit.text) != u8"你好") {
      ok = false;
    }
    if (commit.text)
      api->free_commit(&commit);
  }

  api->destroy_session(session);
  api->finalize();
  if (!ok) {
    std::cerr << "failed to commit the privacy probe candidate\n";
    return 5;
  }
  std::cout << (sensitive ? "sensitive" : "normal")
            << " privacy probe completed\n";
  return 0;
}
