/* Alphabetty — Search panel */

const search = {
    async quickSearch(query) {
        const resp = await fetch(`/api/search?q=${encodeURIComponent(query)}`);
        const data = await resp.json();
        return data.results || [];
    },
};
