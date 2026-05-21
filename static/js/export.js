/* Alphabetty — Export */

const exp = {
    markdown(convId) {
        window.open(`/api/export/markdown/${convId}`, '_blank');
    },

    pdf(convId) {
        window.open(`/api/export/pdf/${convId}`, '_blank');
    },
};

app.exportMarkdown = () => exp.markdown(app.currentConvId);
app.exportPdf = () => exp.pdf(app.currentConvId);
