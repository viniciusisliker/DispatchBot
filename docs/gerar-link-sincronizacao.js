// Gera o link de sincronização (odopen://) da pasta DispatchBot-Fila, para o campo "link_sincronizar" do config.
// Uso (uma vez, na implantação):
//   1. No navegador, abra a biblioteca do SharePoint e ENTRE na pasta DispatchBot-Fila.
//   2. Aperte F12, vá na aba Console, cole este script inteiro e aperte Enter.
//   3. Copie o link que aparecer. Teste: Win+R, cole, Enter -> o OneDrive deve começar a sincronizar a pasta.
// O e-mail de quem gerou vai no link, mas o robô troca pelo e-mail do OneDrive de cada máquina.
(async () => {
  const ctx = window._spPageContextInfo;
  const pasta = new URLSearchParams(location.search).get("id");
  if (!ctx || !pasta) {
    console.error("Abra a pasta DispatchBot-Fila dentro da biblioteca do SharePoint e rode de novo.");
    return;
  }
  const web = ctx.webAbsoluteUrl;
  const json = (url) => fetch(url, { headers: { Accept: "application/json;odata=nometadata" } }).then((r) => {
    if (!r.ok) throw new Error(r.status + " em " + url);
    return r.json();
  });
  const caminho = encodeURIComponent(pasta.replace(/'/g, "''"));
  const infoPasta = await json(`${web}/_api/web/GetFolderByServerRelativePath(decodedurl='${caminho}')?$select=UniqueId,Name,ServerRelativeUrl`);
  const lista = await json(`${web}/_api/web/GetFolderByServerRelativePath(decodedurl='${caminho}')/ListItemAllFields/ParentList?$select=Id,Title`);
  const chaves = (g) => "{" + String(g).replace(/[{}]/g, "") + "}";
  const origem = new URL(web).origin;
  const params = new URLSearchParams({
    siteId: chaves(ctx.siteId),
    webId: chaves(ctx.webId),
    webTitle: ctx.webTitle,
    listId: chaves(lista.Id),
    listTitle: lista.Title,
    userEmail: ctx.userEmail || ctx.userLoginName || "",
    webUrl: web,
    folderId: String(infoPasta.UniqueId).replace(/[{}]/g, ""),
    folderName: infoPasta.Name,
    folderUrl: origem + infoPasta.ServerRelativeUrl,
  });
  const link = "odopen://sync/?" + params.toString();
  console.log("%cLink de sincronização (copie para link_sincronizar no config.json):", "font-weight:bold");
  console.log(link);
  try { copy(link); console.log("(já copiado para a área de transferência)"); } catch (e) {}
})();
