**# Intelbras Allo wT7 — Integração com Home Assistant e Visualizador de Vídeo

Este projeto oferece duas formas de usar o videoporteiro Intelbras Allo wT7:

1. **Integração com o Home Assistant** — permite visualizar a câmera ao vivo, detectar quando a campainha toca, capturar imagens, usar áudio bidirecional e controlar as portas conectadas.
2. **Visualizador independente para Windows** — aplicativo para assistir ao vídeo do wT7 sem executar o Home Assistant. Permite selecionar os canais de câmera 1, 2 e 3.

A integração usa a conta Allo Plus para obter as credenciais do dispositivo. O vídeo ao vivo e os comandos locais são transmitidos diretamente entre e o monitor pela rede local.

## Visualizador de vídeo independente para Windows

O visualizador independente permite assistir à câmera do wT7 em um computador Windows, sem precisar configurar ou executar o Home Assistant. A interface permite:

- Informar o e-mail e a senha da conta Allo Plus.
- Buscar e selecionar um dispositivo vinculado à conta.
- Informar o endereço IP local do monitor.
- Escolher o canal de câmera 1, 2 ou 3.
- Iniciar e encerrar a transmissão de vídeo ao vivo.
- Salvar os dados para preencher automaticamente na próxima vez.

Quando a opção de salvar os dados está ativada, o e-mail e o IP são guardados nas configurações do aplicativo no Windows. A senha é armazenada no **Gerenciador de Credenciais do Windows**, e não em texto simples no arquivo de configurações. Desmarque a opção no aplicativo para remover os dados salvos.

O visualizador consulta o serviço Allo Plus para obter as credenciais temporárias do vídeo. Depois, recebe a transmissão diretamente do monitor pela rede local. O vídeo não passa pelo Home Assistant.

## Atenção: apenas uma sessão de vídeo por vez

O wT7 permite somente uma sessão de vídeo ao vivo por vez. Se a câmera estiver aberta no visualizador, o Home Assistant ou o aplicativo oficial Allo Plus talvez não consiga iniciar outra transmissão até que a sessão atual seja encerrada.

Clique em **Parar** no visualizador quando terminar de assistir.

## Requisitos

- Um monitor Intelbras Allo wT7 conectado à rede local e vinculado ao aplicativo Allo Plus.
- Uma conta Allo Plus funcionando.
- O endereço IP local do monitor.
- Para usar o arquivo `.exe`: um computador com Windows. Não é necessário instalar Python ou FFmpeg separadamente.
- Para executar o visualizador a partir do código-fonte

Consulte as seções abaixo para ver as instruções de instalação, configuração, uso e solução de problemas.**

## Créditos

A integração com o Home Assistant foi baseada no projeto
[homeassistant-allo-wt7](https://github.com/fdaneluzzi/homeassistant-allo-wt7),
criado por [fdaneluzzi](https://github.com/fdaneluzzi). Agradeço ao autor pelo
trabalho e pela contribuição à comunidade de usuários do Intelbras Allo wT7.

Este repositório amplia o projeto com um visualizador independente para Windows,
incluindo seleção entre os canais de câmera 1, 2 e 3.
